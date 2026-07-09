import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _database_url():
    db_user = os.getenv("PGUSER", "agl")
    db_pass = os.getenv("PGPASSWORD", "agl")
    db_name = os.getenv("PGDATABASE", "atmos_gl")
    db_host = os.getenv("PGHOST", "atmos_gl_db")
    db_port = os.getenv("PGPORT", "5432")
    return f"postgresql+psycopg2://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"


engine = create_engine(_database_url())
Session = sessionmaker(bind=engine)
