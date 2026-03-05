import cv2
import numpy as np
from pyzbar.pyzbar import decode


async def read_qr_code(photo_bytes: bytes) -> str | None:
    nparr = np.frombuffer(photo_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    
    if img is None:
        return None
    
    result = decode(img)
    if result:
        return result[0].data.decode('utf-8')
    
    inverted = cv2.bitwise_not(img)
    result = decode(inverted)
    if result:
        return result[0].data.decode('utf-8')
    
    return None
