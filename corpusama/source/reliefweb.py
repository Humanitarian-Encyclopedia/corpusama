import logging

import pandas as pd

from corpusama.database.database import Database
from corpusama.source.call import Call
from corpusama.util import decorator

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

    def all(self, limit: int = 1000):
        """Executes API calls from parameters."""

        self.limit = limit
        self.call_n = 0
        self._set_wait()
        self.one()

    def new(self, limit: int = 1000):
        """Executes calls starting from the latest date.changed value."""

        latest = None
        self.parameters["offset"] = 0
        # ensure proper sorting parameters
        if self.parameters.get("sort", None) != ["date.changed:asc"]:
            m = """Input parameters must be set to retrieve results sorted
            by date.changed in ascending order. Add `"sort": ["date.changed:asc"]`"""
            raise ValueError(m)
        # get most recent date.changed value
        res = self.db.c.execute("SELECT json_extract(_raw.date,'$.changed') FROM _raw")
        self.date_changed_vals = pd.Series(
            [pd.Timestamp(x[0]) for x in res.fetchall()], dtype=object
        )
        self._update_filter()
        # make call
        logger.debug(f"starting from {latest}")
        self.all(limit)

    def _update_filter(self):
        """Updates parameter filters to start from the latest date.changed value."""

        # update if database is not empty
        if self.date_changed_vals.any():
            latest = self.date_changed_vals.max().isoformat()
            # combine new and old conditions
            old_conditions = self.original_parameters.get("filter", {}).get(
                "conditions", []
            )
            new_conditions = [{"field": "date.changed", "value": {"from": latest}}]
            # update parameters
            update = {
                "filter": {
                    "operator": "AND",
                    "conditions": old_conditions + new_conditions,
                }
            }
            self.parameters = self.original_parameters | update

    @decorator.while_loop
    def one(self):
        # set variables
        if "call_n" not in self.__dict__:
            self.call_n = 0
        if "limit" not in self.__dict__:
            self.limit = 1
        if self.call_n >= self.limit:
            return False
        # prepare call
        self._enforce_quota()
        self._offset()
        # make call
        self._request()
        self._get_field_names()
        self._hash()
        keys = ["time", "took", "totalCount", "count"]
        summary = {k: v for k, v in self.response_json.items() if k in keys}
        logger.debug(f"{summary}")
        # store output
        if not self.db:
            self.raw[self.call_n] = self.response_json
        else:
            self.insert()
        self._wait()
        self.call_n += 1
        return True

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
            "api_params": self.parameters,
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
            # add columns
            ids = [[df.iloc[x]["id"]] * len(df.iloc[x]["file"]) for x in range(len(df))]
            df_flat["id"] = [x for y in ids for x in y]
            df_flat = self.db._add_missing_columns(df_flat, "_pdf")
            self.db.insert(df_flat, "_pdf")

    def insert(self):
        """Reshapes and inserts ReliefWeb JSON data into db."""

        # normalize data
        df = pd.json_normalize(self.response_json["data"], sep="_", max_level=1)
        # manage columns
        df.drop(["fields_id"], axis=1, inplace=True, errors="ignore")
        df.columns = [x.replace("fields_", "") for x in df.columns]
        renamed_columns = {
            col: col.replace("-", "_") for col in df.columns if "-" in col
        }
        df.rename(columns=renamed_columns, inplace=True)
        df["api_input"] = self.input.name
        df["api_date"] = self.now
        df["api_params_hash"] = self.hash
        df = self.db._add_missing_columns(df, "_raw")
        self.df_raw = df
        if not df.empty:
            self.db.insert(df, "_raw")
        else:
            raise UserWarning("Call aborted: no more results.")
        self._insert_log()
        self._insert_pdf()

    def __repr__(self):
        return ""

    def __init__(
        self,
        input,
        database=None,
        appname=None,
        url="https://api.reliefweb.int/v1/reports?appname=",
        quota=1000,
        wait_dict={0: 1, 5: 49, 10: 99, 20: 499, 30: None},
    ):
        super().__init__(source="reliefweb")
        # variables
        self.input = input
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
        self._get_parameters()
        self.original_parameters = self.parameters.copy()
