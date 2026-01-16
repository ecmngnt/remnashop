"""
Публичные эндпоинты RemnaWave для Website API.
Позволяют управлять устройствами пользователя.
"""
from typing import Optional
from uuid import UUID

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel

from src.api.endpoints.website import WEBSITE_API_PREFIX, get_current_user_id, verify_api_key
from src.api.schemas.website import ApiResponse
from src.services.remnawave import RemnawaveService
from src.services.subscription import SubscriptionService
from src.services.user import UserService

router = APIRouter(prefix=WEBSITE_API_PREFIX, tags=["RemnaWave API"])


# ============ SCHEMAS ============


class DeviceResponse(BaseModel):
    """Информация об устройстве пользователя."""
    hwid: str
    platform: str
    device_model: str
    os_version: str
    user_agent: str
    created_at: str


class SubscriptionUrlResponse(BaseModel):
    """URL подписки для подключения."""
    subscription_url: str
    qr_code_url: Optional[str] = None


class DeleteDeviceRequest(BaseModel):
    """Запрос на удаление устройства."""
    hwid: str


# ============ ENDPOINTS ============


@router.get("/users/me/devices", response_model=ApiResponse[list[DeviceResponse]])
@inject
async def get_user_devices(
    user_service: FromDishka[UserService],
    remnawave_service: FromDishka[RemnawaveService],
    telegram_id: int = Depends(get_current_user_id),
    api_key_auth: str = Depends(verify_api_key),
):
    """Получение списка устройств пользователя."""
    user = await user_service.get(telegram_id)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Пользователь не найден"
        )
    
    if not user.current_subscription:
        return ApiResponse(
            success=True,
            data=[],
            message="У пользователя нет активной подписки"
        )
    
    devices = await remnawave_service.get_devices_user(user)
    
    result = [
        DeviceResponse(
            hwid=device.hwid,
            platform=device.platform,
            device_model=device.device_model,
            os_version=device.os_version,
            user_agent=device.user_agent,
            created_at=device.created_at.isoformat() if device.created_at else ""
        )
        for device in devices
    ]
    
    return ApiResponse(
        success=True,
        data=result,
        message=f"Найдено устройств: {len(result)}"
    )


@router.delete("/users/me/devices", response_model=ApiResponse[dict])
@inject
async def delete_user_device(
    request: DeleteDeviceRequest,
    user_service: FromDishka[UserService],
    remnawave_service: FromDishka[RemnawaveService],
    telegram_id: int = Depends(get_current_user_id),
    api_key_auth: str = Depends(verify_api_key),
):
    """Удаление устройства пользователя."""
    user = await user_service.get(telegram_id)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Пользователь не найден"
        )
    
    if not user.current_subscription:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="У пользователя нет активной подписки"
        )
    
    result = await remnawave_service.delete_device(user, request.hwid)
    
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Устройство не найдено"
        )
    
    return ApiResponse(
        success=True,
        data={"deleted": True, "remaining_devices": result},
        message=f"Устройство {request.hwid} успешно удалено"
    )


@router.get("/users/me/subscription-url", response_model=ApiResponse[SubscriptionUrlResponse])
@inject
async def get_subscription_url(
    user_service: FromDishka[UserService],
    subscription_service: FromDishka[SubscriptionService],
    remnawave_service: FromDishka[RemnawaveService],
    telegram_id: int = Depends(get_current_user_id),
    api_key_auth: str = Depends(verify_api_key),
):
    """Получение URL подписки для подключения к VPN."""
    user = await user_service.get(telegram_id)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Пользователь не найден"
        )
    
    subscription = user.current_subscription
    
    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="У пользователя нет активной подписки"
        )
    
    # Если URL уже есть в базе
    if subscription.url:
        return ApiResponse(
            success=True,
            data=SubscriptionUrlResponse(
                subscription_url=subscription.url,
                qr_code_url=None
            )
        )
    
    # Получаем URL из RemnaWave
    url = await remnawave_service.get_subscription_url(subscription.user_remna_id)
    
    if not url:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="URL подписки не найден"
        )
    
    # Обновляем в базе
    subscription.url = url
    await subscription_service.update(subscription)
    
    return ApiResponse(
        success=True,
        data=SubscriptionUrlResponse(
            subscription_url=url,
            qr_code_url=None
        )
    )


@router.get("/users/me/subscription-stats", response_model=ApiResponse[dict])
@inject
async def get_subscription_stats(
    user_service: FromDishka[UserService],
    remnawave_service: FromDishka[RemnawaveService],
    telegram_id: int = Depends(get_current_user_id),
    api_key_auth: str = Depends(verify_api_key),
):
    """Получение статистики использования подписки из RemnaWave."""
    user = await user_service.get(telegram_id)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Пользователь не найден"
        )
    
    if not user.current_subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="У пользователя нет активной подписки"
        )
    
    # Получаем актуальные данные из RemnaWave
    remna_user = await remnawave_service.get_user(user.current_subscription.user_remna_id)
    
    if not remna_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Пользователь не найден в RemnaWave"
        )
    
    # Получаем устройства
    devices = await remnawave_service.get_devices_user(user)
    
    traffic_remaining = max(0, remna_user.traffic_limit_bytes - remna_user.used_traffic_bytes) if remna_user.traffic_limit_bytes > 0 else -1
    traffic_percent = (remna_user.used_traffic_bytes / remna_user.traffic_limit_bytes * 100) if remna_user.traffic_limit_bytes > 0 else 0
    
    return ApiResponse(
        success=True,
        data={
            "status": remna_user.status,
            "traffic_used_bytes": remna_user.used_traffic_bytes,
            "traffic_limit_bytes": remna_user.traffic_limit_bytes,
            "traffic_remaining_bytes": traffic_remaining,
            "traffic_percent": round(traffic_percent, 2),
            "device_count": len(devices),
            "device_limit": remna_user.hwid_device_limit or -1,
            "expire_at": remna_user.expire_at.isoformat() if remna_user.expire_at else None,
            "is_active": remna_user.status == "ACTIVE",
        }
    )
