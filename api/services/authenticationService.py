"""
authenticationService.py

This module provides the AuthenticationService class, which encapsulates all business logic
related to user authentication, including sign up, login, third-party provider login,
onboarding, password reset, and logout functionalities.

It raises CustomException with appropriate status codes and UI messages
to be handled at the API layer.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["authenticationService"]


from utils.exceptionHandler import CustomException
from utils.logger import logger
from api.commons import client
from api.models import (
    OnboardingDetails,
    LoginWithProvider,
    NewCredentials,
    Login,
    SignUp
)
from jose import jwt
import datetime
import hashlib
import uuid
import os

class AuthenticationService:
    """
    Service class for user authentication and session management.

    Handles user registration, login, provider-based login, onboarding, password reset, and logout operations.
    Interacts with the Supabase client and manages user/session records in the database.
    """
    def __init__(self) -> None:
        """
        Initialize the AuthenticationService and set up the Supabase client.
        """
        logger.info("Initializing Authentication Service.")
        self.client = client

    @staticmethod
    def _mapSubscriptionStatus(status: str | None) -> str:
        normalized = (status or "").strip().lower()
        mapping = {
            "active": "ACTIVE",
            "renewal_upcoming": "ACTIVE",
            "payment_pending": "ACTIVE",
            "past_due": "PAUSED",
            "suspended": "PAUSED",
            "cancelled": "CANCELLED",
            "expired": "EXPIRED",
        }
        return mapping.get(normalized, "NONE")

    @staticmethod
    def _mapBillingModeToPlan(billingMode: str | None) -> str:
        if billingMode == "monthly_recurring":
            return "pro"
        if billingMode == "annual_prepaid":
            return "annual"
        return "none"

    def _getSubscriptionSnapshot(self, userId: str) -> dict | None:
        response = self.client.table("subscriptions") \
            .select("billing_mode, status, current_period_start, current_period_end") \
            .eq("user_id", userId) \
            .order("updated_at", desc=True) \
            .limit(1) \
            .execute().data
        return response[0] if response else None

    def _createDefaultSubscriptionRow(self, userId: str) -> None:
        """
        Create a canonical placeholder subscription row for a newly created user.

        This enforces the hard-cutover rule: subscription lifecycle fields must be
        sourced from the subscriptions table only (never from Users columns).
        """
        self.client.table("subscriptions").insert({
            "user_id": userId,
            "billing_mode": "monthly_recurring",
            "status": "expired",
            "auto_renew_enabled": False,
            "payment_collection_mode": "authenticated_checkout",
            "default_currency": "INR",
            "current_period_start": None,
            "current_period_end": None,
            "renewal_due_at": None,
        }).execute()

    def _ensureSubscriptionSnapshot(self, userId: str) -> dict:
        """
        Ensure a canonical subscription row exists and return it.
        """
        snapshot = self._getSubscriptionSnapshot(userId)
        if snapshot:
            return snapshot
        try:
            self._createDefaultSubscriptionRow(userId)
        except Exception as createError:
            logger.warning(
                f"Default subscription row create attempt failed for user {userId}: {createError}"
            )
        snapshot = self._getSubscriptionSnapshot(userId)
        if snapshot:
            return snapshot
        raise CustomException(
            ValueError("Missing subscription row"),
            statusCode=409,
            uiMessage="Subscription data is not available. Please contact support."
        )

    def signup(self, signupDetails: SignUp) -> str:
        """
        Register a new user.

        Raises:
            CustomException:
                409 - User already exists
                422 - Invalid signup details
                500 - Generic signup failure
        """
        try:
            if not signupDetails.email or not signupDetails.password:
                raise CustomException(
                    ValueError("Invalid signup payload"),
                    statusCode=422,
                    uiMessage="Invalid signup details. Please check the form."
                )
            passwordString = signupDetails.password + os.environ["SECRET_KEY"]
            hashedPassword = hashlib.md5(passwordString.encode("utf-8")).hexdigest()
            workspaceId = str(uuid.uuid4())
            allUsers = []
            page = 1
            while True:
                response = self.client.auth.admin.list_users(page=page, per_page=1000)
                if response == []:
                    break
                allUsers.extend(response)
                page += 1
            allEmails = [x.email for x in allUsers]
            if signupDetails.email in allEmails:
                raise CustomException(
                    ValueError("User already exists"),
                    statusCode=409,
                    uiMessage="An account with this email already exists."
                )
            response = self.client.auth.sign_up(
                {"email": signupDetails.email, "password": hashedPassword}
            )
            self.client.table("Users").insert({
                "userId": response.user.id,
                "email": signupDetails.email,
                "password": hashedPassword,
                "currentWorkspaceId": workspaceId
            }).execute()
            self.client.table("Workspaces").insert({
                "id": workspaceId,
                "ownerId": response.user.id,
                "ownerEmail": signupDetails.email,
                "workspaceName": "Default"
            }).execute()
            self._ensureSubscriptionSnapshot(response.user.id)
            return response.user.id
        except CustomException:
            raise
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception
        
    def confirmMail(self, userId: str) -> None:
        """
        Resend the confirmation email to the user with the given user ID.

        Args:
            userId (str): The ID of the user to confirm.

        Raises:
            CustomException: For any errors during the process.
        """
        try:
            allUsers = list()
            page = 1
            while True:
                response = self.client.auth.admin.list_users(page = page, per_page = 1000)
                if response == []:
                    break
                else:
                    allUsers.extend(response)
                    page += 1
            email = list(filter(lambda x: True if x.id == userId else False, allUsers))[0].email
            response = self.client.auth.resend({
            "type": "signup",
            "email": email,
            "options": {
                "email_redirect_to": "https://www.nubrixai.com/login"
            }
            })
            return 
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception
        
    def login(self, loginDetails: Login) -> dict:
        """
        Authenticate a user with email and password.

        Raises:
            CustomException:
                401 - Invalid credentials
                422 - Invalid login payload
                500 - Login failure
        """
        try:
            if not loginDetails.email or not loginDetails.password:
                raise CustomException(
                    ValueError("Invalid login payload"),
                    statusCode=422,
                    uiMessage="Invalid login details. Please check the form."
                )
            passwordString = loginDetails.password + os.environ["SECRET_KEY"]
            hashedPassword = hashlib.md5(passwordString.encode("utf-8")).hexdigest()
            allUsers = []
            page = 1
            while True:
                response = self.client.auth.admin.list_users(page=page, per_page=1000)
                if response == []:
                    break
                allUsers.extend(response)
                page += 1
            user = next((x for x in allUsers if x.email == loginDetails.email), None)
            if not user:
                raise CustomException(
                    ValueError("Invalid credentials"),
                    statusCode=401,
                    uiMessage="Email or password is incorrect."
                )
            userRows = self.client.table("Users") \
                .select("userId, email, password, onboarded, currentWorkspaceId") \
                .eq("email", loginDetails.email) \
                .limit(1) \
                .execute().data
            if not userRows:
                raise CustomException(
                    ValueError("Invalid credentials"),
                    statusCode=401,
                    uiMessage="Email or password is incorrect."
                )
            dataSlice = userRows[0]
            if dataSlice.get("password") != hashedPassword:
                raise CustomException(
                    ValueError("Invalid credentials"),
                    statusCode=401,
                    uiMessage="Email or password is incorrect."
                )
            sessionStartTime = datetime.datetime.utcnow()
            tokenPayload = {
                "userId": dataSlice["userId"],
                "email": loginDetails.email,
                "sessionStartTime": str(sessionStartTime)
            }
            accessToken = jwt.encode(tokenPayload, os.environ["SECRET_KEY"], "HS256")
            self.client.table("Sessions").insert({
                "userId": dataSlice["userId"],
                "email": dataSlice["email"],
                "accessToken": accessToken,
                "sessionStartTime": str(sessionStartTime),
                "lastActivity": str(sessionStartTime)
            }).execute()
            subscription = self._ensureSubscriptionSnapshot(dataSlice["userId"])
            subscriptionStatus = self._mapSubscriptionStatus(subscription.get("status") if subscription else None)
            return {
                "status": "SUCCESS",
                "userId": dataSlice["userId"],
                "email": dataSlice["email"],
                "accessToken": accessToken,
                "onboarded": int(bool(dataSlice.get("onboarded"))),
                "currentWorkspaceId": dataSlice["currentWorkspaceId"],
                "subscriptionStatus": subscriptionStatus,
                "subscriptionStart": subscription.get("current_period_start") if subscription else None,
                "subscriptionExpiry": subscription.get("current_period_end") if subscription else None,
                "subscriptionPlan": self._mapBillingModeToPlan(subscription.get("billing_mode") if subscription else None)
            }
        except CustomException:
            raise
        except Exception as e:
            exception = CustomException(
                e,
                uiMessage="Login failed. Please try again later."
            )
            logger.error(exception)
            raise exception
        
    def loginWithProvider(self, loginDetails: LoginWithProvider) -> dict:
        """
        Authenticate or register a user using a third-party provider (Google/GitHub).
        
        If the user does not exist:
        - Creates a new user record with a 12-day free trial.
        - Creates a default workspace.
        - Logs them in.
        
        If the user exists:
        - Logs them in and returns the standard session details.

        Raises:
            CustomException:
                422 - Invalid provider payload
                500 - Provider login failure
        """
        try:
            if not loginDetails.email:
                raise CustomException(
                    ValueError("Invalid provider login payload"),
                    statusCode=422,
                    uiMessage="Invalid login details. Please check the form."
                )

            response = self.client.table("Users").select("*").eq("email", loginDetails.email).execute()
            userData = {}
            sessionStartTime = datetime.datetime.utcnow()

            if response.data:
                userData = response.data[0]
                subscription = self._ensureSubscriptionSnapshot(userData["userId"])
                subscriptionStatus = self._mapSubscriptionStatus(subscription.get("status") if subscription else None)
                subscriptionPlan = self._mapBillingModeToPlan(subscription.get("billing_mode") if subscription else None)
            else:
                userId = str(uuid.uuid4())
                workspaceId = str(uuid.uuid4())
                passwordString = f"{loginDetails.sub}{loginDetails.id}{loginDetails.nodeId}{os.environ['SECRET_KEY']}"
                hashedPassword = hashlib.md5(passwordString.encode("utf-8")).hexdigest()
                
                userData = {
                    "userId": userId,
                    "email": loginDetails.email,
                    "password": hashedPassword,
                    "createdAt": str(sessionStartTime),
                    "onboarded": False,
                    "currentWorkspaceId": workspaceId
                }
                self.client.table("Users").insert(userData).execute()
                self.client.table("Workspaces").insert({
                    "id": workspaceId,
                    "ownerId": userId,
                    "ownerEmail": loginDetails.email,
                    "workspaceName": "Default"
                }).execute()
                subscription = self._ensureSubscriptionSnapshot(userId)
                subscriptionStatus = self._mapSubscriptionStatus(subscription.get("status"))
                subscriptionPlan = self._mapBillingModeToPlan(subscription.get("billing_mode"))

            tokenPayload = {
                "userId": userData["userId"],
                "email": userData["email"],
                "sessionStartTime": str(sessionStartTime)
            }
            accessToken = jwt.encode(tokenPayload, os.environ["SECRET_KEY"], "HS256")
            self.client.table("Sessions").insert({
                "userId": userData["userId"],
                "email": userData["email"],
                "accessToken": accessToken,
                "sessionStartTime": str(sessionStartTime),
                "lastActivity": str(sessionStartTime)
            }).execute()

            return {
                "status": "SUCCESS",
                "userId": userData["userId"],
                "email": userData["email"],
                "accessToken": accessToken,
                "onboarded": 1 if userData.get("onboarded") else 0,
                "currentWorkspaceId": userData["currentWorkspaceId"],
                "subscriptionStatus": subscriptionStatus,
                "subscriptionPlan": subscriptionPlan 
            }
        except CustomException:
            raise
        except Exception as e:
            exception = CustomException(
                e,
                uiMessage="Login with provider failed. Please try again later."
            )
            logger.error(exception)
            raise exception
        
    def onboarding(self, onboardingDetails = OnboardingDetails) -> None:
        """
        Update user onboarding details in the database.

        Args:
            onboardingDetails (OnboardingDetails): The user's onboarding information.

        Raises:
            CustomException: For any errors during onboarding update.
        """
        try:
            dataToUpdate = {
                "onboarded": 1,
                "usage": onboardingDetails.usage,
                "fullName": onboardingDetails.fullName,
                "role": onboardingDetails.role,
                "companyName": onboardingDetails.companyName,
                "industryType": onboardingDetails.industryType,
                "companySize": onboardingDetails.companySize,
                "country": onboardingDetails.country,
                "goals": onboardingDetails.goals,
                "source": onboardingDetails.source
            }
            response = self.client.table("Users").update(dataToUpdate).eq("email", onboardingDetails.email).execute()
            return 
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception        
        
    def initiatePasswordReset(self, emailId: str) -> None:
        """
        Initiate a password reset process for the given email address.

        Args:
            emailId (str): The email address of the user requesting password reset.

        Raises:
            CustomException: For any errors during password reset initiation.
        """
        try:
            self.client.auth.reset_password_for_email(
                emailId,
                {
                    "redirect_to": "https://www.nubrixai.com/login/create-new-password"
                }
            )
            return 
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception
        
    def resetPassword(self, newCredentials: NewCredentials) -> None:
        """
        Reset the user's password with new credentials.

        Args:
            newCredentials (NewCredentials): The new password and user email.

        Raises:
            CustomException: For any errors during password reset.
        """
        try:
            passwordString = newCredentials.newPassword + os.environ["SECRET_KEY"]
            hashedPassword = hashlib.md5(passwordString.encode("utf-8")).hexdigest()
            allUsers = list()
            page = 1
            while True:
                response = self.client.auth.admin.list_users(page = page, per_page = 1000)
                if response == []:
                    break
                else:
                    allUsers.extend(response)
                    page += 1
            filteredResult = list(filter(lambda x: True if x.email == newCredentials.email else False, allUsers))[0]
            response = self.client.auth.admin.update_user_by_id(
                filteredResult.id,
                {"password": hashedPassword}
            )
            response = self.client.table("Users").update({"password": hashedPassword}).eq("email", newCredentials.email).execute()
            return
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception
    
    def logout(self, token: str) -> None:
        """
        Log out a user by deleting their session.

        Raises:
            CustomException:
                401 - User not logged in
                500 - Logout failure
        """
        try:
            if not token:
                raise CustomException(
                    ValueError("No active session"),
                    statusCode=401,
                    uiMessage="You are not logged in."
                )
            self.client.table("Sessions").delete().eq("accessToken", token).execute()
            return
        except CustomException:
            raise
        except Exception as e:
            exception = CustomException(
                e,
                uiMessage="Logout failed. Try again later."
            )
            logger.error(exception)
            raise exception

authenticationService = AuthenticationService()
