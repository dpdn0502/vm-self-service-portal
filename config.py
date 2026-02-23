import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Flask
    SECRET_KEY      = os.getenv('FLASK_SECRET_KEY', 'fallback-secret')

    # Azure Entra ID
    CLIENT_ID       = os.getenv('AZURE_CLIENT_ID')
    CLIENT_SECRET   = os.getenv('AZURE_CLIENT_SECRET')
    TENANT_ID       = os.getenv('AZURE_TENANT_ID')

    # Azure Subscription
    SUBSCRIPTION_ID = os.getenv('AZURE_SUBSCRIPTION_ID')

    # MSAL — just for login
    AUTHORITY       = f"https://login.microsoftonline.com/{os.getenv('AZURE_TENANT_ID')}"
    REDIRECT_URI    = "http://localhost:5000/auth/callback"
    SCOPE           = ["User.Read"]   # ← back to simple User.Read for login only

    import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Flask
    SECRET_KEY      = os.getenv('FLASK_SECRET_KEY', 'fallback-secret')

    # Azure Entra ID
    CLIENT_ID       = os.getenv('AZURE_CLIENT_ID')
    CLIENT_SECRET   = os.getenv('AZURE_CLIENT_SECRET')
    TENANT_ID       = os.getenv('AZURE_TENANT_ID')

    # Azure Subscription
    SUBSCRIPTION_ID = os.getenv('AZURE_SUBSCRIPTION_ID')

    # MSAL
    AUTHORITY       = f"https://login.microsoftonline.com/{os.getenv('AZURE_TENANT_ID')}"
    REDIRECT_URI    = "http://localhost:5000/auth/callback"
    SCOPE           = ["User.Read"]

    # Database — SQLite for now, Azure SQL later
    SQLALCHEMY_DATABASE_URI         = 'sqlite:///vmportal.db'
    SQLALCHEMY_TRACK_MODIFICATIONS  = False

    # ServiceNow — NEW
    SNOW_INSTANCE_URL = os.getenv('SNOW_INSTANCE_URL')
    SNOW_USERNAME     = os.getenv('SNOW_USERNAME')
    SNOW_PASSWORD     = os.getenv('SNOW_PASSWORD')





