import asyncio
import logging
import traceback
from datetime import datetime
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from app.utils.config import config

logger = logging.getLogger(__name__)

_client: Optional[gspread.Client] = None


def get_client() -> gspread.Client:
    global _client
    if _client is None:
        if not config.google_sheet_id or not config.google_credentials_path:
            raise ValueError("Google Sheet ID or credentials path not configured")
        
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        
        credentials = Credentials.from_service_account_file(
            config.google_credentials_path,
            scopes=scopes
        )
        
        _client = gspread.authorize(credentials)
    
    return _client


def get_worksheet():
    client = get_client()
    spreadsheet = client.open_by_key(config.google_sheet_id)
    return spreadsheet.sheet1


async def append_new_esim(esim_id: int, provider: str, lpa_string: str, supplier: str = ""):
    try:
        await asyncio.to_thread(_append_new_esim_sync, esim_id, provider, lpa_string, supplier)
    except Exception as e:
        logger.error(f"Failed to append eSIM {esim_id} to Google Sheet: {e}\n{traceback.format_exc()}")


def _append_new_esim_sync(esim_id: int, provider: str, lpa_string: str, supplier: str = ""):
    worksheet = get_worksheet()
    
    now = datetime.now()
    formatted_date = now.strftime("%d.%m.%Y %H:%M")
    
    row = [
        esim_id,
        formatted_date,
        provider,
        lpa_string,
        supplier,
        "🟢 Available",
        "",
        ""
    ]
    
    worksheet.append_row(row, value_input_option="USER_ENTERED")


async def update_esim_status(
    esim_id: int,
    status: str,
    user_identifier: str = "",
    issued_at: str = ""
):
    try:
        await asyncio.to_thread(
            _update_esim_status_sync,
            esim_id,
            status,
            user_identifier,
            issued_at
        )
    except Exception as e:
        logger.error(f"Failed to update eSIM {esim_id} status in Google Sheet: {e}\n{traceback.format_exc()}")


def _update_esim_status_sync(
    esim_id: int,
    status: str,
    user_identifier: str = "",
    issued_at: str = ""
):
    worksheet = get_worksheet()
    
    all_values = worksheet.get_all_values()
    
    row_index = None
    for idx, row in enumerate(all_values, start=1):
        if row and str(row[0]) == str(esim_id):
            row_index = idx
            break
    
    if row_index is None:
        logger.warning(f"eSIM {esim_id} not found in Google Sheet")
        return
    
    worksheet.update_cell(row_index, 6, status)
    worksheet.update_cell(row_index, 7, user_identifier)
    worksheet.update_cell(row_index, 8, issued_at)


async def update_esim_status_only(esim_id: int, status: str):
    try:
        await asyncio.to_thread(
            _update_esim_status_only_sync,
            esim_id,
            status
        )
    except Exception as e:
        logger.error(f"Failed to update eSIM {esim_id} status in Google Sheet: {e}\n{traceback.format_exc()}")


def _update_esim_status_only_sync(esim_id: int, status: str):
    worksheet = get_worksheet()
    
    all_values = worksheet.get_all_values()
    
    row_index = None
    for idx, row in enumerate(all_values, start=1):
        if row and str(row[0]) == str(esim_id):
            row_index = idx
            break
    
    if row_index is None:
        logger.warning(f"eSIM {esim_id} not found in Google Sheet")
        return
    
    worksheet.update_cell(row_index, 6, status)
