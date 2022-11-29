import logging
import pathlib
import re
import sqlite3 as sql

from datamgr.util import convert

logger = logging.getLogger(__name__)


class Database:
    """A class to connect to a database and execute queries."""

    def open_db(self):
        """Opens SQL database connection."""

        self.conn = sql.connect(self.path)
        self.c = self.conn.cursor()
        logger.debug(f"{self.path}")

    def close_db(self):
        """Closes SQL database connection."""

        self.c.execute("pragma optimize")
        self.c.close()
        self.conn.close()
        logger.debug(f"{self.path}")

    def get_schema(self):
        """Gets any .sql files in ./** to a dict at self.schema."""

        self.schema = pathlib.Path("").glob("**/*.sql")
        self.schema = {x.stem: {"path": x} for x in self.schema}
        for v in self.schema.values():
            with open(v["path"], "r") as f:
                v["query"] = f.read()
        logger.debug(f"{len(self.schema)}")

    def get_tables(self):
        """Makes a dict of {table:[columns]} at self.tables.

        Uses select.sql statements to supply valid tables."""

        self.tables = {}
        res = self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        # get tables if db not empty
        if [x[0] for x in res.fetchall()]:
            queries = [
                "SELECT * FROM _log",
                "SELECT * FROM _pdf",
                "SELECT * FROM _raw",
                "SELECT * FROM _vert",
                "SELECT * FROM _archive",
            ]
            for q in queries:
                # add table
                key = re.search(r"\w+$", q).group()
                try:
                    self.tables[key] = [x[0] for x in self.c.execute(q).description]
                except sql.OperationalError:
                    logger.debug(f"{key} does not exist")

        logger.debug(f"{self.tables}")

    def insert(self, df, table):
        """Inserts/replaces a df into a table with a standardized insert command.

        Converts nan-like values to None, then converts all values to str."""

        # standardize datatypes
        df = df.astype(str)
        df = convert.nan_to_none(df)
        # insert into SQL
        records = df[self.tables[table]].to_records(index=False)
        n_columns = len(self.tables[table])
        values = ",".join(list("?" * n_columns))
        self.c.executemany(
            f"INSERT OR REPLACE INTO {table} VALUES ({values})",
            records,
        )
        self.conn.commit()
        logger.debug(f"{len(df)} row(s) into {table}")

    def __repr__(self):
        return ""

    def __init__(
        self,
        db,
        dir="data",
    ):
        # variables
        self.path = pathlib.Path(dir) / pathlib.Path(db)
        self.df = {}
        # execute
        self.path.parent.mkdir(exist_ok=True)
        self.open_db()
        self.get_schema()
        self.get_tables()
