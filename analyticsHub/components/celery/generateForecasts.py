# importing libraries
from sqlalchemy import create_engine
from lightgbm import LGBMRegressor
from functools import lru_cache
from tqdm import tqdm
import pandas as pd
import numpy as np
import statistics
import requests
import warnings
import datetime
import logging
import base64
import os

warnings.filterwarnings(action = "ignore")

def generateAndSendForecasts():
    engine = create_engine(
        f'postgresql+psycopg2://{os.environ.get("POSTGRE_USER")}:{os.environ.get("POSTGRE_PASSWORD")}@{os.environ.get("POSTGRE_HOST")}:{os.environ.get("POSTGRE_PORT")}/{os.environ.get("POSTGRE_DB")}'
    )

    dfOrig = pd.read_sql("emptykegsdata", engine).rename(columns={"sum(Quantity)": "Quantity"})
    data = dfOrig.copy()

    data["Quantity"] = data["Quantity"].astype(float)

    # separating individual series
    logging.info("Separating data into individual series")
    completeData = {}
    for series, dataSlice in data.groupby(by = ["Name", "SizeId"]):
        completeData[series] = dataSlice

    # adjusting week 53 from all series
    logging.info("Adjusting inconsistencies in the 12th month (53rd week, rows with month number as 12 but week number as 1, etc)")
    def monthTransformationFunction(row):  
        if (row["MonthNumber"] == 12) and (row["WeekOfYear"] == 1):
            return 1
        else: return row["MonthNumber"]
    for seriesName in tqdm(completeData, desc = "FORMATTING SERIES : "):
        series = completeData[seriesName]
        series["MonthNumber"] = series.apply(monthTransformationFunction, axis = 1)
        series = series.groupby(by = ["year", "MonthNumber", "WeekOfYear"])["Quantity"].sum().reset_index(drop = False)
        week53 = series[series["WeekOfYear"] == 53]
        for idx, row in week53.iterrows():
            nextRecord = series[(
                (series["year"] == row["year"] + 1) &
                (series["MonthNumber"] == 1) &
                (series["WeekOfYear"] == 1)
            )]
            if len(nextRecord) >= 1:
                totalQuantity = nextRecord["Quantity"].iloc[0] + row["Quantity"]
                series.loc[len(series), :] = [row["year"] + 1, 1, 1, totalQuantity]
            else:
                series.loc[len(series), :] = [row["year"] + 1, 1, 1, row["Quantity"]]
        series = series[series["WeekOfYear"] != 53]
        series = series.sort_values(by = ["year", "MonthNumber", "WeekOfYear"], ascending = True).reset_index(drop = True)
        series["year"] = series["year"].astype(int)
        series["MonthNumber"] = series["MonthNumber"].astype(int)
        series["WeekOfYear"] = series["WeekOfYear"].astype(int)
        completeData[seriesName] = series

    # function to obtain month number out of week
    lru_cache(maxsize=512)
    def getMonthNumber(year, week):
        return int(statistics.mode([
            datetime.date.fromisocalendar(year=int(year), week=int(week), day=day).month
            for day in range(1, 8)
        ]))

    ### removing missing values
    logging.info("Handling inconsistencies and removing missing values")
    for seriesName in tqdm(completeData, desc = "HANDLING INCONSISTENCIES: "):
        series = completeData[seriesName]
        startYear, startWeek, endYear, endWeek = (
            series.loc[0, "year"],
            series.loc[0, "WeekOfYear"],
            series.loc[len(series) - 1, "year"],
            series.loc[len(series) - 1, "WeekOfYear"]
        )
        years, weeks = list(), list()
        year, week = startYear, startWeek
        while True:
            years.append(year)
            weeks.append(week)
            if ((year == endYear) & (week == endWeek)):
                break
            elif week == 52:
                year += 1
                week = 1
            else:
                week += 1
        allTimestamps = pd.DataFrame(data = {
            "year": years,
            "WeekOfYear": weeks
        })
        series = pd.merge(left = series, right = allTimestamps, on = ["year", "WeekOfYear"], how = "right")
        series["Quantity"] = series["Quantity"].interpolate()
        series["MonthNumber"] = series.apply(lambda row: getMonthNumber(row["year"], row["WeekOfYear"]), axis = 1)
        completeData[seriesName] = series

    # performing feature extraction (EWMA, Lags, Deltas, MA, STD)
    logging.info("Performing feature extraction")
    def featureExtraction(dataset: pd.DataFrame, windowSize: int = 12):
        df = dataset.copy()
        for i in range(1, windowSize + 1):
            df[f"lag{i}"] = df["Quantity"].shift(i)
        for i in range(48, 48 + 8 + 1):
            df[f"lag{i}"] = df["Quantity"].shift(i)
        df[f"rollingMean{windowSize}"] = df["lag1"].rolling(windowSize).mean()
        df[f"rollingStd{windowSize}"] = df["lag1"].rolling(windowSize).std()
        df[f"ewmMean{windowSize}"] = df["lag1"].ewm(alpha = 0.6).mean()
        df[f"ewmStd{windowSize}"] = df["lag1"].ewm(alpha = 0.6).std()
        df["delta1"] = df["lag1"].diff(1)
        df["delta2"] = df["lag1"].diff(2)
        return df

    # generating a test record for single inferencing
    def generateTestRecord(dataset: pd.DataFrame, windowSize: int = 12):
        currentWeek = dataset["WeekOfYear"].iloc[-1]
        year = dataset["year"].iloc[-1]
        if currentWeek != 52:
            week = currentWeek + 1
        else:
            year += 1
            week = 1
        month = statistics.mode([datetime.date.fromisocalendar(year = int(year), week = int(week), day = x).month for x in range(1, 8)])
        lags = pd.Series([dataset.iloc[-1, :]["Quantity"]] + dataset.loc[dataset.index[-1], [f"lag{x}" for x in range(1, windowSize)]].tolist())
        lags_pr = pd.Series([dataset["Quantity"].iloc[x] for x in range(-48, -48 - 8 - 1, -1)])
        rollingMean = lags.mean()
        rollingStd = lags.std()
        EwmMean = lags[::-1].ewm(alpha = 0.6).mean().iloc[-1]
        EwmStd = lags[::-1].ewm(alpha = 0.6).std().iloc[-1]
        delta1 = lags.iloc[0] - lags.iloc[1]
        delta2 = lags.iloc[0] - lags.iloc[2]
        testRecord = [year, month, week] + lags.tolist() + lags_pr.tolist() + [rollingMean, rollingStd, EwmMean, EwmStd, delta1, delta2]
        return testRecord

    # generating predictions using Light Gradient Bossting Machine Regressor
    logging.info("Generating predictions using LGBMRegressor")
    def generatePredictions(dataset: pd.DataFrame, windowSize: int = 12, forecastHorizon: int = 4):
        dfOrig = dataset.copy()
        dfOrig["Quantity"] = np.log(dfOrig["Quantity"] + 0.0001)
        for i in range(forecastHorizon):
            df = featureExtraction(dataset = dfOrig, windowSize = windowSize)
            if len(df) > 1:
                model = LGBMRegressor(n_estimators=500, n_jobs=-1, verbose=-1)
                model.fit(X = df.dropna().drop("Quantity", axis = 1), y = df.dropna()["Quantity"])
                testRecord = generateTestRecord(dataset = df, windowSize = windowSize)
                prediction = model.predict(np.array(testRecord).reshape((1, -1)))[0]
                dfOrig.loc[len(dfOrig)] = [testRecord[0], testRecord[1], testRecord[2], prediction]
            else:
                pass
        dfOrig["Quantity"] = np.exp(dfOrig["Quantity"]) - 0.0001
        return dfOrig.tail(forecastHorizon)

    completePredictions = dict()

    # running the inference on all time series
    logging.info("Running inference on all time series")
    for seriesName in tqdm(list(completeData.keys())[:10]):
        try:
            series = completeData[seriesName]
            if ((series.iloc[-1, 0] == 2024) & (len(series) >= 60)):
                nRows, nLags = 60, 12
                predictions = pd.DataFrame()
                seriesLength = len(series)
                while nRows <= seriesLength:
                    predictionsNew = generatePredictions(dataset = series.iloc[:nRows, :4], windowSize = nLags, forecastHorizon = 2)
                    predictions = pd.concat([predictions, predictionsNew], axis = 0)
                    nRows += 2
                predictions.rename(columns = {"Quantity": "predictions"}, inplace = True)
                series = pd.merge(left = series, right = predictions, how = "left", on = ["year", "WeekOfYear"]).drop(["MonthNumber_y"], axis = 1).rename(columns = {"MonthNumber_x": "MonthNumber"})
                predictionsNew = generatePredictions(dataset = series.iloc[:, :4], windowSize = 12, forecastHorizon = 2).rename(columns = {"Quantity": "predictions"})
                series = pd.concat([series, predictionsNew], axis = 0)
                series.replace(to_replace = [np.inf], value = [np.nan], inplace = True)
                series["predictions"] = series["predictions"].interpolate()
                completePredictions[seriesName]  = series
            else:
                continue
        except Exception as e:
            logging.error(f"SERIES {seriesName} SAYS: {e}")

    for seriesName in completePredictions.keys():
        completePredictions[seriesName]["Name"] = [seriesName[0]] * len(completePredictions[seriesName])
        completePredictions[seriesName]["SizeId"] = [seriesName[1]] * len(completePredictions[seriesName])

    # saving the output to a csv
    logging.info("Saving output to CSV file")
    df = pd.concat([completePredictions[x] for x in completePredictions.keys()], axis = 0)
    df = df[["year", "WeekOfYear", "Name", "SizeId", "Quantity", "predictions"]]
    df.rename(columns = {"Quantity": "real"}, inplace = True)

    # Adding month feature
    df["MonthNumber"] = df.apply(lambda row: getMonthNumber(row["year"], row["WeekOfYear"]), axis = 1)

    # Adding ParentName
    df = pd.merge(left = df, right = dfOrig[["ParentName", "Name"]], on =  "Name", how = "left")
    df = df[["year", "MonthNumber", "WeekOfYear", "ParentName", "Name", "SizeId", "real", "predictions"]]
    df.drop_duplicates(keep = "first", inplace = True)
    df = df.rename(columns = {"real": "Actual", "predictions": "Forecast"})
    b64String = base64.b64encode(df.to_csv(index = False).encode()).decode()

    # Prepare the payload
    payload = {
        "sender": {
            "name": "Rauhan Ahmed",
            "email": "admin@rauhanahmed.in"
        },
        "to": [
            {"email": "defa22200@gmail.com", "name": "Rauhan"}
        ],
        "subject": "CSV Output File - AnalyticsHub",
        "htmlContent": """
            <html>
                <body style="font-family: Arial, sans-serif; background-color: #f9f9f9; padding: 20px; color: #333;">
                    <div style="max-width: 600px; margin: auto; background-color: #ffffff; padding: 30px; border: 1px solid #ddd; border-radius: 6px;">
                        <p style="font-size: 16px; margin-bottom: 20px;">Dear Recipient,</p>
                        <p style="font-size: 15px; margin-bottom: 20px;">
                            Please find the attached CSV file containing the output data.
                        </p>
                        <p style="font-size: 15px;">Best regards,<br><strong>Rauhan Ahmed</strong></p>
                    </div>
                </body>
            </html>
        """,
        "attachment": [
            {
                "name": "outputLGBM.csv",
                "content": b64String
            }
        ]
    }

    # Send the request
    response = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={
            "accept": "application/json",
            "api-key": os.environ.get("BREVO_API_KEY"),
            "content-type": "application/json"
        },
        json=payload
    )

    return response.status_code