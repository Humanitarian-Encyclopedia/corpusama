import logging

import pandas as pd

from datamgr.database.database import Database
from datamgr.source.call import Call

logger = logging.getLogger(__name__)


class ReliefWeb(Call):
    """Manages API calls made to ReliefWeb.

    Usage:
    ```python
    job = ReliefWeb(<input>, "<database.db>")
    job.run()
    ```

    Options
    - input = JSON/YML filepath or dict with parameters
    - database = None or database filename
    - n_calls = number of calls to make (pages to collect)
    - appname = unique identifier for using ReliefWeb's API
    - url = base url for making POST calls
    - quota = daily usage limit (see ReliefWeb API documentation)
    - wait_dict = dict of wait times for ranges of n_calls
    (default {0: 1, 5: 49, 10: 99, 20: 499, 30: None})"""

    def _offset(self):
        """Adjusts offset parameter and halts job if no more results."""

        if self.call_n > 0:
            self.parameters["offset"] += self.response_json["count"]
            if self.response_json["count"] == 0:
                raise UserWarning("Call aborted: no more results.")
        logger.debug(self.parameters["offset"])

    def run(self):
        """Makes API call(s) and stores response_json in dict self.raw."""

        for call_n in range(self.n_calls):
            # execute call and prepare data
            self.call_n = call_n
            self._enforce_quota()
            self._offset()
            self._request()
            self._get_field_names()
            self._hash()
            keys = ["time", "took", "totalCount", "count"]
            summary = {k: v for k, v in self.response_json.items() if k in keys}
            logger.debug(f"{summary}")
            # store output
            if not self.db:
                self.raw[call_n] = self.response_json
            else:
                self.insert()
            self._wait()

    def _get_field_names(self):
        """Makes a set of field names from response data."""

        self.field_names = set()
        for x in self.response_json["data"]:
            self.field_names.update(list(x["fields"].keys()))

        logger.debug(f"{len(self.field_names)} {sorted(self.field_names)}")

    def _insert_log(self):
        """Inserts a log entry for a call."""

        record = {
            "api_params_hash": self.hash,
            "parameters": self.parameters,
            "api_input": self.input.name,
            "api_date": self.now,
            "count": self.response_json["count"],
            "total_count": self.response_json["totalCount"],
        }
        self.df_log = pd.DataFrame.from_records([record])
        self.db.insert(self.df_log, "_log")

    def _insert_pdf(self):
        """Updates pdf table after each call."""

        df = self.df_raw.loc[self.df_raw["file"].notna()].copy()
        if not df.empty:
            # make 1 row per file
            df_flat = pd.json_normalize(df["file"].explode())
            df_flat.rename(columns={"id": "file_id"}, inplace=True)
            # add report id column
            ids = [[df.iloc[x]["id"]] * len(df.iloc[x]["file"]) for x in range(len(df))]
            df_flat["id"] = [x for y in ids for x in y]
            self.db.insert(df_flat, "_pdf")

    def insert(self):
        """Reshapes and inserts ReliefWeb JSON data into db."""

        # normalize data
        df = pd.json_normalize(self.response_json["data"], sep="_", max_level=1)
        # manage columns
        df.drop(["id"], axis=1, inplace=True, errors=False)
        df.columns = [x.replace("fields_", "") for x in df.columns]
        renamed_columns = {
            col: col.replace("-", "_") for col in df.columns if "-" in col
        }
        df.rename(columns=renamed_columns, inplace=True)
        df["api_input"] = self.input.name
        df["api_date"] = self.now
        df["api_params_hash"] = self.hash
        for x in [x for x in self.db.tables["_raw"] if x not in df.columns]:
            df[x] = None
        self.df_raw = df
        self.db.insert(df, "_raw")
        self._insert_log()
        self._insert_pdf()

    def __repr__(self):
        return ""

    def __init__(
        self,
        input,
        database=None,
        n_calls=1,
        appname=None,
        url="https://api.reliefweb.int/v1/reports?appname=",
        quota=1000,
        wait_dict={0: 1, 5: 49, 10: 99, 20: 499, 30: None},
    ):
        super().__init__(source="reliefweb")
        # variables
        self.input = input
        self.n_calls = n_calls
        self.url = url
        self.quota = quota
        self.wait_dict = wait_dict
        self.db_name = database
        self.db = None
        self.raw = {}
        self.log = {}
        if not appname:
            self.appname = self.config["reliefweb"]["appname"]
        else:
            self.appname = appname
        # prepare job
        if database:
            self.db = Database(database)
            if not self.db.tables:
                self.db.c.executescript(self.db.schema["reliefweb"]["query"])
                self.db.get_tables()
        self.url = "".join([self.url, self.appname])
        self._set_wait()
        self._get_parameters()
