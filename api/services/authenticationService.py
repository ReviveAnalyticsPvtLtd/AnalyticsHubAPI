"""
authenticationService.py

This module provides the AuthenticationService class, which encapsulates all business logic related to user authentication, including sign up, login, third-party provider login, onboarding, password reset, and logout functionalities. It interacts with the Supabase client and manages user and session records in the database.
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
import pandas as pd
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

    def signup(self, signupDetails: SignUp) -> str:
        """
        Register a new user with the provided signup details.

        Args:
            signupDetails (SignUp): The user's signup information (email, password, etc.).

        Returns:
            str: The user ID of the newly registered user.

        Raises:
            ValueError: If the user already exists.
            CustomException: For any other errors during signup.
        """
        try:
            passwordString = signupDetails.password + os.environ["SECRET_KEY"]
            hashedPassword = hashlib.md5(passwordString.encode("utf-8")).hexdigest()
            workspaceId = str(uuid.uuid4())
            allUsers = list()
            page = 1
            while True:
                response = self.client.auth.admin.list_users(page = page, per_page = 1000)
                if response == []:
                    break
                else:
                    allUsers.extend(response)
                    page += 1
            allUsers = [x.email for x in allUsers]
            if signupDetails.email not in allUsers:
                response = self.client.auth.sign_up(
                    {"email": signupDetails.email, "password": hashedPassword}
                )
                self.client.table(table_name = "Users").insert(
                    {
                        "userId": response.user.id,
                        "email": signupDetails.email,
                        "password": hashedPassword,
                        "currentWorkspaceId": workspaceId
                    }
                ).execute()
                _ = self.client.table("Workspaces").insert({
                    "id": workspaceId,
                    "ownerId": response.user.id,
                    "ownerEmail": signupDetails.email,
                    "workspaceName": "Default"
                }).execute()
                return response.user.id
            else:
                raise ValueError("User Already Exists")
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
                "email_redirect_to": "https://localhost:3000/login"
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

        Args:
            loginDetails (Login): The user's login credentials.

        Returns:
            dict: A dictionary containing authentication status, user info, and access token.

        Raises:
            ValueError: If the user is not found, email is not verified, or credentials are invalid.
            CustomException: For any other errors during login.
        """
        try:
            passwordString = loginDetails.password + os.environ["SECRET_KEY"]
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
            filteredResult = list(filter(lambda x: True if x.email == loginDetails.email else False, allUsers))
            if filteredResult == []:
                raise ValueError("User not found")
            elif filteredResult[0].user_metadata.get("email_verified") == False:
                raise ValueError("Email not verified")
            else:  
                allData = pd.DataFrame(self.client.table("Users").select("userId", "email", "password", "onboarded", "currentWorkspaceId", "createdAt", "subscriptionStart", "subscriptionExpiry", "subscriptionPlan").execute().data, columns = ["userId", "email", "password", "onboarded", "currentWorkspaceId", "createdAt", "subscriptionStart", "subscriptionExpiry", "subscriptionPlan"])
                dataSlice = allData[allData["email"] == loginDetails.email].iloc[0, :]
                if dataSlice["password"] != hashedPassword:
                    raise ValueError("Invalid email or password")
                else:
                    sessionStartTime = datetime.datetime.utcnow()
                    dictItems = {
                        "userId": dataSlice["userId"],
                        "email": loginDetails.email,
                        "password": hashedPassword,
                        "sessionStartTime": str(sessionStartTime)
                    }
                    accessToken = jwt.encode(dictItems, os.environ["SECRET_KEY"], "HS256")
                    self.client.table("Sessions").insert({
                        "userId": dataSlice["userId"],
                        "email": dataSlice["email"],
                        "accessToken": accessToken,
                        "sessionStartTime": str(sessionStartTime),
                        "lastActivity": str(sessionStartTime)
                    }).execute()
                    if dataSlice["subscriptionExpiry"] is None:
                        subscriptionStatus = "INACTIVE"
                    else:
                        if pd.to_datetime(dataSlice["subscriptionExpiry"]) >= pd.to_datetime(datetime.datetime.utcnow()):
                            subscriptionStatus = "ACTIVE"
                        else:
                            subscriptionStatus = "INACTIVE"
                    response = {
                        "status": "SUCCESS",
                        "userId": dataSlice["userId"],
                        "email": dataSlice["email"],
                        "accessToken": accessToken,
                        "onboarded": int(dataSlice["onboarded"]),
                        "currentWorkspaceId": dataSlice["currentWorkspaceId"],
                        "subscriptionStatus": subscriptionStatus,
                        "subscriptionStart": str(dataSlice["subscriptionStart"]),
                        "subscriptionExpiry": str(dataSlice["subscriptionExpiry"]),
                        "subscriptionPlan": dataSlice["subscriptionPlan"]
                    }
            return response
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception
        
    def loginWithProvider(self, loginDetails: LoginWithProvider) -> dict:
        """
        Authenticate or register a user using a third-party provider.

        Args:
            loginDetails (LoginWithProvider): The provider's login details.

        Returns:
            dict: A dictionary containing authentication status, user info, and access token.

        Raises:
            CustomException: For any errors during provider login.
        """
        try:
            passwordString = str(loginDetails.sub) + str(loginDetails.id) + str(loginDetails.nodeId) + os.environ["SECRET_KEY"]
            hashedPassword = hashlib.md5(passwordString.encode("utf-8")).hexdigest()
            registeredUsers = pd.DataFrame(self.client.table("Users").select("userId", "email", "password", "onboarded", "currentWorkspaceId", "createdAt", "subscriptionStart", "subscriptionExpiry", "subscriptionPlan").execute().data, columns = ["userId", "email", "password", "onboarded", "currentWorkspaceId", "createdAt", "subscriptionStart", "subscriptionExpiry", "subscriptionPlan"])
            if loginDetails.email not in registeredUsers["email"].unique():
                response = self.client.table(table_name = "Users").insert(
                    {
                        "email": loginDetails.email,
                        "password": hashedPassword
                    }
                ).execute()
                registeredUsers = pd.DataFrame(self.client.table("Users").select("userId", "email", "password", "onboarded", "currentWorkspaceId", "createdAt", "subscriptionStart", "subscriptionExpiry", "subscriptionPlan").execute().data, columns = ["userId", "email", "password", "onboarded", "currentWorkspaceId", "createdAt", "subscriptionStart", "subscriptionExpiry", "subscriptionPlan"])
            else:
                pass
            dataSlice = registeredUsers[registeredUsers["email"] == loginDetails.email].iloc[0, :]
            sessionStartTime = str(datetime.datetime.utcnow())
            dictItems = {
                "userId": dataSlice["userId"],
                "email": loginDetails.email,
                "password": hashedPassword,
                "sessionStartTime": sessionStartTime
            }
            accessToken = jwt.encode(dictItems, os.environ["SECRET_KEY"], "HS256")
            self.client.table("Sessions").insert({
                "userId": dataSlice["userId"],
                "email": dataSlice["email"],
                "accessToken": accessToken,
                "sessionStartTime": sessionStartTime,
                "lastActivity": sessionStartTime
            }).execute()
            response = {
                "status": "SUCCESS",
                "userId": dataSlice["userId"],
                "email": dataSlice["email"],
                "accessToken": accessToken,
                "onboarded": int(dataSlice["onboarded"])
            }
            return response
        except Exception as e:
            exception = CustomException(e)
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
                    "redirect_to": "http://localhost:3000/login/create-new-password"
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
        Log out the user by deleting their session using the provided access token.

        Args:
            token (str): The access token of the session to be terminated.
        """
        self.client.table("Sessions").delete().eq("accessToken", token).execute()
        return

authenticationService = AuthenticationService()  