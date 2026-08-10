import os
from pathlib import Path
import dj_database_url
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / '.env')

BASE_DIR = Path(__file__).resolve().parent.parent

# Fail fast if required secrets are absent — prevents insecure fallback deploys
SECRET_KEY = os.environ['SECRET_KEY']

DEBUG = os.environ.get('DEBUG', 'False') == 'True'

_raw_hosts = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1,192.168.1.150')
ALLOWED_HOSTS = [h.strip() for h in _raw_hosts.split(',') if h.strip()]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',
    'django.contrib.sitemaps',
    # Allauth
    'allauth',
    'allauth.account',
    'django_ratelimit',
    # Third-party
    'rest_framework',
    # Local apps
    'panel',
    'accounts',
    'catalog',
    'orders',
    'core',
    'api',
    'negocio',
]

SITE_ID = 1

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

MIDDLEWARE = [
    'core.middleware.MaintenanceModeMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'allauth.account.middleware.AccountMiddleware',
    'core.middleware.ContentSecurityPolicyMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.cart_count',
                'core.context_processors.active_categories',
                'core.context_processors.site_config',
                'core.context_processors.meta_pixel',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

_db_url = os.environ.get('DATABASE_URL')
if _db_url:
    DATABASES = {'default': dj_database_url.parse(_db_url, conn_max_age=600)}
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# Rate limiting works with LocMemCache on single-process servers (dev + single-worker prod).
# For multi-worker prod, replace with Redis: django-redis + CACHES using RedisCache.
SILENCED_SYSTEM_CHECKS = ['django_ratelimit.E003', 'django_ratelimit.W001']

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'es-mx'
TIME_ZONE = 'America/Mexico_City'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        # Los endpoints públicos se marcan explícitamente con @permission_classes([AllowAny])
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
}

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/'

# ——— Allauth ———
ACCOUNT_LOGIN_METHODS       = {'username', 'email'}
ACCOUNT_SIGNUP_FIELDS       = ['email*', 'username*', 'password1*', 'password2*']
ACCOUNT_EMAIL_VERIFICATION  = 'none'
ACCOUNT_LOGOUT_ON_GET       = False  # Require POST — prevents CSRF logout via GET
ACCOUNT_SIGNUP_FORM_CLASS   = 'accounts.forms.CustomSignupForm'
ACCOUNT_ADAPTER             = 'accounts.adapters.AccountAdapter'

# WhatsApp business number (without + or spaces)
WHATSAPP_NUMBER = '521XXXXXXXXXX'

# URL del servidor /notify del bot de WhatsApp (persona2 en prod — la única
# instancia con ORDERS_GROUP_ID configurado; ver bot/bot.js:startNotifyServer).
# persona2 DEBE correr con NOTIFY_PORT=8953 (ver bot/DEPLOY.md, Fase 3) — el
# default de NOTIFY_PORT es 8952 y ese puerto lo ocupa persona1, que NO tiene
# ORDERS_GROUP_ID configurado.
BOT_NOTIFY_URL = os.environ.get('BOT_NOTIFY_URL', 'http://127.0.0.1:8953')

# URL pública del sitio — se usa para armar links en avisos (WhatsApp, etc).
SITE_URL = os.environ.get('SITE_URL', 'http://localhost:8000')

# ——— Upload limits ———
DATA_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024   # 20 MB per request body
FILE_UPLOAD_MAX_MEMORY_SIZE = 15 * 1024 * 1024   # 15 MB before spooling to disk
MAX_UPLOAD_SIZE             = 15 * 1024 * 1024   # 15 MB per single file (enforced in views)

# ——— Session security ———
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE      = 60 * 60 * 24 * 14  # 2 weeks

# ——— Headers seguros (siempre activos) ———
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'

# Always active — Nginx sets X-Forwarded-Proto; required for HTTPS detection behind proxy
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Trusted origins for CSRF — set in .env for production (e.g. https://ryalsneackers.com)
_csrf_origins = os.environ.get('CSRF_TRUSTED_ORIGINS', '')
if _csrf_origins:
    CSRF_TRUSTED_ORIGINS = [o.strip() for o in _csrf_origins.split(',') if o.strip()]

# ——— Cache — Redis in production, LocMemCache in dev ———
_redis_url = os.environ.get('REDIS_URL', '')
if _redis_url:
    CACHES = {
        'default': {
            'BACKEND': 'django_redis.cache.RedisCache',
            'LOCATION': _redis_url,
            'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient'},
        }
    }
    SILENCED_SYSTEM_CHECKS = []  # Redis is multi-worker safe — checks no longer needed

# ——— Logging — errores Django → stderr → journald ———
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'WARNING',
    },
    'loggers': {
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
        'django.security': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
    },
}

# ——— Production security headers (activated when DEBUG=False) ———
if not DEBUG:
    SECURE_SSL_REDIRECT             = True
    SESSION_COOKIE_SECURE           = True
    CSRF_COOKIE_SECURE              = True
    SECURE_HSTS_SECONDS             = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS  = True
    SECURE_HSTS_PRELOAD             = True
    SECURE_CONTENT_TYPE_NOSNIFF     = True
    X_FRAME_OPTIONS                 = 'DENY'

NEGOCIO_API_KEY = os.environ['NEGOCIO_API_KEY']

# ID del Meta Pixel para tracking de anuncios de Facebook e Instagram.
# Valor vacio por defecto: el pixel no se renderiza si no esta configurado.
META_PIXEL_ID = os.environ.get('META_PIXEL_ID', '')
