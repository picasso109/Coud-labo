import os


class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'coud-labo-secret-2026')
    SQLALCHEMY_DATABASE_URI = 'sqlite:///coud_labo.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False