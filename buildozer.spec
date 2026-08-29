[app]

# (str) Title of your application
title = Saha Operasyon Merkezi

# (str) Package name
package.name = sahamobil

# (str) Package domain (needed for android/ios packaging)
package.domain = org.saha

# (str) Source code where the main.py lives
source.dir = .

# (list) Source files to include (crucial for json and png files)
source.include_exts = py,png,jpg,kv,atlas,json

# (str) Application versioning
version = 0.1

# (list) Application requirements
requirements = python3,kivy,google-api-python-client,google-auth,google-auth-httplib2,requests,urllib3,charset_normalizer,idna,openssl,cryptography

# (str) Supported orientations
orientation = portrait

# (bool) Indicate whether the screen should be fullscreen
fullscreen = 0

# (list) Permissions
android.permissions = INTERNET,WRITE_EXTERNAL_STORAGE,READ_EXTERNAL_STORAGE

# (int) Target Android API
android.api = 34

# (str) Android build tools version to use
android.build_tools_version = 34.0.0

# (int) Minimum API supported
android.minapi = 21

# (bool) Automatically accept SDK licenses
android.accept_sdk_license = True

# (list) Architectures to build for (building arm64-v8a only cuts compilation time in half)
android.archs = arm64-v8a

# (bool) Allow backup
android.allow_backup = True

[buildozer]

# (int) Log level (0 = error only, 1 = info, 2 = debug)
log_level = 2

# (int) Display warning if buildozer is run as root
warn_on_root = 1