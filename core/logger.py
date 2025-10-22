import csv, os, datetime

class CsvLogger:
    def __init__(self, path="reports/run.csv"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        if not os.path.exists(path):
            with open(path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(["timestamp","site","stage","status","detail"])

    def log(self, site:str, stage:str, status:str, detail:str=""):
        with open(self.path, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([datetime.datetime.now().isoformat(), site, stage, status, detail])
