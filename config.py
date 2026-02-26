import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Flask
    SECRET_KEY = os.getenv('FLASK_SECRET_KEY', 'fallback-secret')
    DEBUG = os.environ.get('FLASK_DEBUG', 'True') == 'True'

    # Azure Entra ID
    CLIENT_ID = os.getenv('AZURE_CLIENT_ID')
    CLIENT_SECRET = os.getenv('AZURE_CLIENT_SECRET')
    TENANT_ID = os.getenv('AZURE_TENANT_ID')

    # Azure Subscription
    SUBSCRIPTION_ID = os.getenv('AZURE_SUBSCRIPTION_ID')

    # MSAL
    AUTHORITY = f"https://login.microsoftonline.com/{os.getenv('AZURE_TENANT_ID')}"
    REDIRECT_URI = "http://localhost:5000/auth/callback"
    SCOPE = ["User.Read"]

    # Database — SQLite for now, Azure SQL later
    SQLALCHEMY_DATABASE_URI = 'sqlite:///vmportal.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ServiceNow — NEW
    SNOW_INSTANCE_URL = os.getenv('SNOW_INSTANCE_URL')
    SNOW_USERNAME = os.getenv('SNOW_USERNAME')
    SNOW_PASSWORD = os.getenv('SNOW_PASSWORD')

    # Approvers — notification emails
    APPROVER1_EMAIL = os.getenv('APPROVER1_EMAIL')
    APPROVER2_EMAIL = os.getenv('APPROVER2_EMAIL')

    # Approvers — Azure login emails (for portal access)
    APPROVER1_AZURE = os.getenv('APPROVER1_AZURE')
    APPROVER2_AZURE = os.getenv('APPROVER2_AZURE')

    # Email
    MAIL_SERVER = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.getenv('MAIL_PORT', 587))
    MAIL_USE_TLS = True
    MAIL_USERNAME = os.getenv('MAIL_USERNAME')
    MAIL_PASSWORD = os.getenv('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.getenv('MAIL_DEFAULT_SENDER')








