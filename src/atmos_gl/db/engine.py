import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _database_url():
    db_user = os.getenv("POSTGRES_USER", "agl")
    db_pass = os.getenv("POSTGRES_PASSWORD", "agl")
    db_name = os.getenv("POSTGRES_DB", "atmos_gl")
    db_host = os.getenv("POSTGRES_HOST", "atmos_gl_db")
    db_port = os.getenv("POSTGRES_PORT", "5432")
    return f"postgresql+psycopg2://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"


engine = create_engine(_database_url())
Session = sessionmaker(bind=engine)
