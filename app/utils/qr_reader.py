"""
Ultimate QR Code Scanner for eSIM Telegram Bot
Using WeChatQRCode (CNN-based) with pyzbar fallback pipeline

Model Files (Caffe format):
- detect.prototxt: https://raw.githubusercontent.com/WeChatCV/opencv_3rdparty/wechat_qrcode/detect.prototxt
- detect.caffemodel: https://raw.githubusercontent.com/WeChatCV/opencv_3rdparty/wechat_qrcode/detect.caffemodel
- sr.prototxt: https://raw.githubusercontent.com/WeChatCV/opencv_3rdparty/wechat_qrcode/sr.prototxt
- sr.caffemodel: https://raw.githubusercontent.com/WeChatCV/opencv_3rdparty/wechat_qrcode/sr.caffemodel
"""

import asyncio
import os
import cv2
import numpy as np
from pyzbar.pyzbar import decode
from typing import Optional
from pathlib import Path


MODEL_BASE_URL = "https://raw.githubusercontent.com/WeChatCV/opencv_3rdparty/wechat_qrcode"

MODEL_FILES = {
    "detect.prototxt": f"{MODEL_BASE_URL}/detect.prototxt",
    "detect.caffemodel": f"{MODEL_BASE_URL}/detect.caffemodel",
    "sr.prototxt": f"{MODEL_BASE_URL}/sr.prototxt",
    "sr.caffemodel": f"{MODEL_BASE_URL}/sr.caffemodel",
}

_wechat_detector: Optional[cv2.wechat_qrcode_WeChatQRCode] = None


def _get_models_dir() -> Path:
    """Get or create directory for WeChatQRCode models."""
    models_dir = Path(__file__).parent.parent.parent / "models"
    models_dir.mkdir(exist_ok=True)
    return models_dir


def _download_model_file(filename: str, url: str, models_dir: Path) -> Path:
    """Download a model file if it doesn't exist."""
    filepath = models_dir / filename
    if filepath.exists() and filepath.stat().st_size > 0:
        return filepath
    
    import urllib.request
    print(f"Downloading {filename}...")
    try:
        urllib.request.urlretrieve(url, filepath)
        print(f"Downloaded {filename}")
    except Exception as e:
        print(f"Failed to download {filename}: {e}")
        if filepath.exists():
            filepath.unlink()
        raise
    return filepath


async def _init_wechat_detector() -> cv2.wechat_qrcode_WeChatQRCode:
    """Initialize WeChatQRCode detector with Caffe models."""
    global _wechat_detector
    
    if _wechat_detector is not None:
        return _wechat_detector
    
    models_dir = _get_models_dir()
    
    detect_prototxt = _download_model_file("detect.prototxt", MODEL_FILES["detect.prototxt"], models_dir)
    detect_caffemodel = _download_model_file("detect.caffemodel", MODEL_FILES["detect.caffemodel"], models_dir)
    sr_prototxt = _download_model_file("sr.prototxt", MODEL_FILES["sr.prototxt"], models_dir)
    sr_caffemodel = _download_model_file("sr.caffemodel", MODEL_FILES["sr.caffemodel"], models_dir)
    
    _wechat_detector = cv2.wechat_qrcode_WeChatQRCode(
        str(detect_prototxt),
        str(detect_caffemodel),
        str(sr_prototxt),
        str(sr_caffemodel)
    )
    print("WeChatQRCode detector initialized")
    return _wechat_detector


def _try_wechat_decode(img: np.ndarray, detector: cv2.wechat_qrcode_WeChatQRCode) -> Optional[str]:
    """Try to decode using WeChatQRCode."""
    try:
        # WeChatQRCode returns (decoded_strings_list, bounding_boxes_list)
        decoded_list, _ = detector.detectAndDecode(img)
        if decoded_list and len(decoded_list) > 0:
            return decoded_list[0]
    except Exception as e:
        pass
    return None


def _try_pyzbar_decode(img: np.ndarray) -> Optional[str]:
    """Try to decode using pyzbar."""
    try:
        result = decode(img)
        if result:
            return result[0].data.decode('utf-8')
    except Exception:
        pass
    return None


def _process_grayscale(img: np.ndarray) -> np.ndarray:
    """Convert to grayscale."""
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _process_invert(img: np.ndarray) -> np.ndarray:
    """Invert colors (for white-on-dark QR codes)."""
    return cv2.bitwise_not(img)


def _process_clahe(img: np.ndarray) -> np.ndarray:
    """Apply CLAHE for contrast enhancement."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def _process_threshold(img: np.ndarray) -> np.ndarray:
    """Apply thresholding to remove glare."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return thresh


def _robust_decode_sync(photo_bytes: bytes, detector: cv2.wechat_qrcode_WeChatQRCode) -> Optional[str]:
    """Synchronous robust QR code decoding with fallback pipeline."""
    nparr = np.frombuffer(photo_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    
    if img is None:
        return None
    
    processing_steps = [
        ("original", lambda x: x),
        ("grayscale", _process_grayscale),
        ("inverted", _process_invert),
        ("clahe", _process_clahe),
        ("threshold", _process_threshold),
    ]
    
    for step_name, processor in processing_steps:
        try:
            processed = processor(img)
            
            decoded = _try_wechat_decode(processed, detector)
            if decoded:
                return decoded
            
            if len(processed.shape) == 2:
                processed_color = cv2.cvtColor(processed, cv2.COLOR_GRAY2BGR)
            else:
                processed_color = processed
            
            decoded = _try_wechat_decode(processed_color, detector)
            if decoded:
                return decoded
                
        except Exception:
            pass
    
    for step_name, processor in processing_steps:
        try:
            processed = processor(img)
            if len(processed.shape) == 3:
                processed = cv2.cvtColor(processed, cv2.COLOR_BGR2GRAY)
            decoded = _try_pyzbar_decode(processed)
            if decoded:
                return decoded
        except Exception:
            pass
    
    inverted_gray = cv2.bitwise_not(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    decoded = _try_pyzbar_decode(inverted_gray)
    if decoded:
        return decoded
    
    return None


async def read_qr_code(photo_bytes: bytes) -> Optional[str]:
    """
    Asynchronous ultimate QR code decoder using WeChatQRCode.
    
    Uses WeChatQRCode (CNN-based) as primary decoder for maximum robustness against:
    - Logos/chips in center
    - Stylized markers (rounded dots)
    - Screen distortions (moire, glare)
    - Inverted colors
    
    Falls back to pyzbar with preprocessing pipeline if WeChatQRCode fails.
    
    Args:
        photo_bytes: Raw image bytes from Telegram photo/document
        
    Returns:
        Decoded QR code string or None if decoding failed
    """
    try:
        detector = await _init_wechat_detector()
        return await asyncio.to_thread(_robust_decode_sync, photo_bytes, detector)
    except Exception as e:
        print(f"QR decoding error: {e}")
        return None