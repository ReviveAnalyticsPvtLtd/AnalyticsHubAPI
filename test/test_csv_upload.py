import unittest

from api.services.dataLoadService import DataLoadService


class CsvUploadTests(unittest.TestCase):
    def test_csv_reader_detects_semicolon_delimited_uploads(self):
        df = DataLoadService._readCsvUpload(
            b'"age";"job";"balance"\n30;"unemployed";1787\n'
        )

        self.assertEqual(["age", "job", "balance"], list(df.columns))
        self.assertEqual("unemployed", df.loc[0, "job"])


if __name__ == "__main__":
    unittest.main()
