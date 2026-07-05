from sqlalchemy import create_engine, Column, Integer, String, DateTime, func
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = 'sqlite:///shortener.db'

# connect_args for SQLite so SQLAlchemy can be used from multiple threads (Flask dev server)
engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

Base = declarative_base()

class URL(Base):
    __tablename__ = 'urls'
    id = Column(Integer, primary_key=True)
    long_url = Column(String, nullable=False)
    short_code = Column(String, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

def init_db():
    Base.metadata.create_all(bind=engine)