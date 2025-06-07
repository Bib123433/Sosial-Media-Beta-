import os
import cloudinary
import cloudinary.uploader
import cloudinary.api

# Konfigurasi Cloudinary
cloudinary.config(
    cloud_name   = os.getenv("CLOUDINARY_CLOUD_NAME", "dfdexhtzf"),
    api_key      = os.getenv("CLOUDINARY_API_KEY",    "456377639935236"),
    api_secret   = os.getenv("CLOUDINARY_API_SECRET", "io01Gx_DSnVyLxFGulbF3Hv5dkU"),
    secure       = True
)

# (Opsional) Fungsi upload helper
def upload_to_cloudinary(file_path):
    result = cloudinary.uploader.upload(file_path)
    return result
