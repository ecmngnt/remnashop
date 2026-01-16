from typing import Optional

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, Depends, Header, HTTPException, status
from loguru import logger

from src.api.schemas.website import (
    ApiResponse,
    AuthResponse,
    BalanceResponse,
    CreatePaymentRequest,
    GatewayResponse,
    PaymentResponse,
    PaymentStatusResponse,
    PlanDurationPriceResponse,
    PlanDurationResponse,
    PlanResponse,
    ReferralStatsResponse,
    SettingsResponse,
    SubscriptionConfigResponse,
    SubscriptionResponse,
    TelegramAuthRequest,
    TransactionResponse,
    UserResponse,
)
from src.api.utils.auth import create_jwt_token, verify_jwt_token, verify_telegram_auth
from src.core.config import AppConfig
from src.core.constants import API_V1
from src.core.enums import PurchaseType, ReferralRewardType
from src.infrastructure.database.models.dto import PlanSnapshotDto
from src.services.payment_gateway import PaymentGatewayService
from src.services.plan import PlanService
from src.services.pricing import PricingService
from src.services.referral import ReferralService
from src.services.subscription import SubscriptionService
from src.services.transaction import TransactionService
from src.services.user import UserService

WEBSITE_API_PREFIX = API_V1 + "/website"

router = APIRouter(prefix=WEBSITE_API_PREFIX, tags=["Website API"])


@inject
async def verify_api_key(
    config: FromDishka[AppConfig],
    x_api_key: str = Header(..., alias="X-API-Key"),
) -> str:
    """Проверка API ключа для доступа к Website API."""
    if not config.website.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Website API отключен"
        )
    
    if x_api_key != config.website.api_key.get_secret_value():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный API ключ"
        )
    return x_api_key


@inject
async def get_current_user_id(
    config: FromDishka[AppConfig],
    authorization: str = Header(...),
) -> int:
    """Получение telegram_id из JWT токена."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный формат токена"
        )
    
    token = authorization[7:]
    telegram_id = verify_jwt_token(token, config.website.jwt_secret.get_secret_value())
    
    if telegram_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Невалидный или истёкший токен"
        )
    
    return telegram_id


@router.get("/plans", response_model=ApiResponse[list[PlanResponse]])
@inject
async def get_plans(
    plan_service: FromDishka[PlanService],
    api_key_auth: str = Depends(verify_api_key),
):
    """Получение списка активных тарифных планов."""
    plans = await plan_service.get_all()
    
    result = []
    for plan in plans:
        if not plan.is_active:
            continue
        
        durations = []
        for duration in plan.durations:
            prices = [
                PlanDurationPriceResponse(
                    currency=price.currency.value,
                    price=str(price.price)
                )
                for price in duration.prices
            ]
            durations.append(PlanDurationResponse(
                id=duration.id,
                days=duration.days,
                prices=prices
            ))
        
        result.append(PlanResponse(
            id=plan.id,
            name=plan.name,
            description=plan.description,
            type=plan.type.value,
            traffic_limit=plan.traffic_limit,
            device_limit=plan.device_limit,
            is_active=plan.is_active,
            durations=durations
        ))
    
    return ApiResponse(success=True, data=result)


@router.get("/plans/{plan_id}", response_model=ApiResponse[PlanResponse])
@inject
async def get_plan(
    plan_id: int,
    plan_service: FromDishka[PlanService],
    api_key_auth: str = Depends(verify_api_key),
):
    """Получение информации о конкретном тарифном плане."""
    plan = await plan_service.get(plan_id)
    
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"План с ID {plan_id} не найден"
        )
    
    durations = []
    for duration in plan.durations:
        prices = [
            PlanDurationPriceResponse(
                currency=price.currency.value,
                price=str(price.price)
            )
            for price in duration.prices
        ]
        durations.append(PlanDurationResponse(
            id=duration.id,
            days=duration.days,
            prices=prices
        ))
    
    result = PlanResponse(
        id=plan.id,
        name=plan.name,
        description=plan.description,
        type=plan.type.value,
        traffic_limit=plan.traffic_limit,
        device_limit=plan.device_limit,
        is_active=plan.is_active,
        durations=durations
    )
    
    return ApiResponse(success=True, data=result)


@router.post("/users/auth", response_model=ApiResponse[AuthResponse])
@inject
async def authenticate_user(
    request: TelegramAuthRequest,
    config: FromDishka[AppConfig],
    user_service: FromDishka[UserService],
    api_key_auth: str = Depends(verify_api_key),
):
    """Авторизация пользователя через Telegram Login Widget."""
    auth_data = request.model_dump()
    
    if not verify_telegram_auth(auth_data, config.bot.token.get_secret_value()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверные данные авторизации Telegram"
        )
    
    user = await user_service.get(request.id)
    is_new_user = False
    
    if not user:
        is_new_user = True
        logger.info(f"Создание нового пользователя через Website API: {request.id}")
    
    token, expires_at = create_jwt_token(
        request.id,
        config.website.jwt_secret.get_secret_value(),
        config.website.jwt_expires_hours
    )
    
    user_response = UserResponse(
        telegram_id=request.id,
        name=f"{request.first_name} {request.last_name or ''}".strip(),
        username=request.username,
        language=user.language.value if user else config.default_locale.value,
        personal_discount=user.personal_discount if user else 0,
        points=user.points if user else 0,
        has_subscription=user.has_subscription if user else False,
    )
    
    return ApiResponse(
        success=True,
        data=AuthResponse(
            user=user_response,
            token=token,
            expires_at=expires_at,
            is_new_user=is_new_user
        ),
        message="Для завершения регистрации запустите Telegram бота" if is_new_user else None
    )


@router.get("/users/me", response_model=ApiResponse[UserResponse])
@inject
async def get_current_user(
    user_service: FromDishka[UserService],
    subscription_service: FromDishka[SubscriptionService],
    telegram_id: int = Depends(get_current_user_id),
    api_key_auth: str = Depends(verify_api_key),
):
    """Получение информации о текущем авторизованном пользователе."""
    user = await user_service.get(telegram_id)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Пользователь не найден"
        )
    
    subscription = None
    if user.current_subscription:
        sub = user.current_subscription
        traffic_remaining = max(0, sub.traffic_limit - sub.traffic_used) if sub.traffic_limit > 0 else -1
        traffic_percent = (sub.traffic_used / sub.traffic_limit * 100) if sub.traffic_limit > 0 else 0
        
        subscription = SubscriptionResponse(
            id=sub.id,
            plan_name=sub.plan.name if sub.plan else "Unknown",
            status=sub.status.value,
            traffic_limit=sub.traffic_limit,
            traffic_used=sub.traffic_used,
            traffic_remaining=traffic_remaining,
            traffic_percent=round(traffic_percent, 2),
            device_limit=sub.device_limit,
            created_at=sub.created_at,
            expire_at=sub.expire_at,
            days_remaining=sub.days_remaining,
            subscription_url=sub.url
        )
    
    return ApiResponse(
        success=True,
        data=UserResponse(
            telegram_id=user.telegram_id,
            name=user.name,
            username=user.username,
            language=user.language.value,
            personal_discount=user.personal_discount,
            points=user.points,
            has_subscription=user.has_subscription,
            subscription=subscription
        )
    )


@router.get("/payments/gateways", response_model=ApiResponse[list[GatewayResponse]])
@inject
async def get_payment_gateways(
    payment_service: FromDishka[PaymentGatewayService],
    api_key_auth: str = Depends(verify_api_key),
):
    """Получение списка доступных платёжных шлюзов."""
    gateways = await payment_service.get_all()
    
    result = [
        GatewayResponse(
            type=gw.type.value.lower(),
            name=gw.type.value,
            currency=gw.currency.value,
            is_active=gw.is_active
        )
        for gw in gateways if gw.is_active
    ]
    
    return ApiResponse(success=True, data=result)


@router.post("/payments/create", response_model=ApiResponse[PaymentResponse])
@inject
async def create_payment(
    request: CreatePaymentRequest,
    user_service: FromDishka[UserService],
    plan_service: FromDishka[PlanService],
    payment_service: FromDishka[PaymentGatewayService],
    pricing_service: FromDishka[PricingService],
    api_key_auth: str = Depends(verify_api_key),
):
    """Создание платежа для подписки."""
    user = await user_service.get(request.telegram_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Пользователь не найден. Сначала запустите Telegram бота."
        )
    
    plan = await plan_service.get(request.plan_id)
    if not plan or not plan.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="План не найден или неактивен"
        )
    
    duration = plan.get_duration(request.duration_days)
    if not duration:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Длительность {request.duration_days} дней недоступна для этого плана"
        )
    
    gateway = await payment_service.get_by_type(request.gateway_type)
    if not gateway or not gateway.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Платёжный шлюз недоступен"
        )
    
    currency = request.currency or gateway.currency
    price = duration.get_price(currency)
    pricing = pricing_service.calculate(user, price, currency)
    
    transaction_plan = PlanSnapshotDto.from_plan(plan, duration.days)
    
    try:
        result = await payment_service.create_payment(
            user=user,
            plan=transaction_plan,
            pricing=pricing,
            purchase_type=request.purchase_type,
            gateway_type=request.gateway_type,
        )
        
        return ApiResponse(
            success=True,
            data=PaymentResponse(
                payment_id=str(result.id),
                payment_url=result.url,
                amount=str(pricing.final_amount),
                currency=currency.value,
                gateway_type=request.gateway_type.value.lower(),
                status="pending",
                expires_at=result.expires_at
            )
        )
    except Exception as e:
        logger.exception(f"Ошибка создания платежа: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Ошибка создания платежа: {str(e)}"
        )


@router.get("/settings/public", response_model=ApiResponse[SettingsResponse])
@inject
async def get_public_settings(
    config: FromDishka[AppConfig],
    api_key_auth: str = Depends(verify_api_key),
):
    """Получение публичных настроек для сайта."""
    return ApiResponse(
        success=True,
        data=SettingsResponse(
            default_currency="USD",
            supported_currencies=["USD", "RUB"],
            telegram_bot_username="netivo_bot",  # TODO: получать из Bot API
            support_url=None
        )
    )


# ============ НОВЫЕ ЭНДПОИНТЫ ============


@router.get("/users/me/subscriptions", response_model=ApiResponse[list[SubscriptionResponse]])
@inject
async def get_user_subscriptions(
    subscription_service: FromDishka[SubscriptionService],
    telegram_id: int = Depends(get_current_user_id),
    api_key_auth: str = Depends(verify_api_key),
):
    """Получение списка всех подписок пользователя."""
    subscriptions = await subscription_service.get_all_by_user(telegram_id)
    
    result = []
    for sub in subscriptions:
        traffic_remaining = max(0, sub.traffic_limit - sub.traffic_used) if sub.traffic_limit > 0 else -1
        traffic_percent = (sub.traffic_used / sub.traffic_limit * 100) if sub.traffic_limit > 0 else 0
        
        result.append(SubscriptionResponse(
            id=sub.id,
            plan_name=sub.plan.name if sub.plan else "Unknown",
            status=sub.status.value,
            traffic_limit=sub.traffic_limit,
            traffic_used=sub.traffic_used,
            traffic_remaining=traffic_remaining,
            traffic_percent=round(traffic_percent, 2),
            device_limit=sub.device_limit,
            created_at=sub.created_at,
            expire_at=sub.expire_at,
            days_remaining=sub.days_remaining,
            subscription_url=sub.url
        ))
    
    return ApiResponse(success=True, data=result)


@router.get("/users/me/subscriptions/{subscription_id}", response_model=ApiResponse[SubscriptionResponse])
@inject
async def get_subscription_details(
    subscription_id: int,
    subscription_service: FromDishka[SubscriptionService],
    telegram_id: int = Depends(get_current_user_id),
    api_key_auth: str = Depends(verify_api_key),
):
    """Получение детальной информации о конкретной подписке."""
    subscription = await subscription_service.get(subscription_id)
    
    if not subscription or subscription.user.telegram_id != telegram_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Подписка не найдена"
        )
    
    traffic_remaining = max(0, subscription.traffic_limit - subscription.traffic_used) if subscription.traffic_limit > 0 else -1
    traffic_percent = (subscription.traffic_used / subscription.traffic_limit * 100) if subscription.traffic_limit > 0 else 0
    
    result = SubscriptionResponse(
        id=subscription.id,
        plan_name=subscription.plan.name if subscription.plan else "Unknown",
        status=subscription.status.value,
        traffic_limit=subscription.traffic_limit,
        traffic_used=subscription.traffic_used,
        traffic_remaining=traffic_remaining,
        traffic_percent=round(traffic_percent, 2),
        device_limit=subscription.device_limit,
        created_at=subscription.created_at,
        expire_at=subscription.expire_at,
        days_remaining=subscription.days_remaining,
        subscription_url=subscription.url
    )
    
    return ApiResponse(success=True, data=result)


@router.get("/users/me/subscriptions/{subscription_id}/config", response_model=ApiResponse[SubscriptionConfigResponse])
@inject
async def get_subscription_config(
    subscription_id: int,
    subscription_service: FromDishka[SubscriptionService],
    telegram_id: int = Depends(get_current_user_id),
    api_key_auth: str = Depends(verify_api_key),
):
    """Получение конфигурации VPN для подписки (ключи подключения)."""
    subscription = await subscription_service.get(subscription_id)
    
    if not subscription or subscription.user.telegram_id != telegram_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Подписка не найдена"
        )
    
    if not subscription.url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Конфигурация подписки недоступна"
        )
    
    return ApiResponse(
        success=True,
        data=SubscriptionConfigResponse(
            subscription_url=subscription.url,
            qr_code_url=None  # TODO: можно добавить генерацию QR кода
        )
    )


@router.post("/users/me/subscriptions/{subscription_id}/renew", response_model=ApiResponse[PaymentResponse])
@inject
async def renew_subscription(
    subscription_id: int,
    request: CreatePaymentRequest,
    user_service: FromDishka[UserService],
    subscription_service: FromDishka[SubscriptionService],
    plan_service: FromDishka[PlanService],
    payment_service: FromDishka[PaymentGatewayService],
    pricing_service: FromDishka[PricingService],
    telegram_id: int = Depends(get_current_user_id),
    api_key_auth: str = Depends(verify_api_key),
):
    """Продление существующей подписки."""
    subscription = await subscription_service.get(subscription_id)
    
    if not subscription or subscription.user.telegram_id != telegram_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Подписка не найдена"
        )
    
    user = await user_service.get(telegram_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Пользователь не найден"
        )
    
    plan = await plan_service.get(request.plan_id)
    if not plan or not plan.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="План не найден или неактивен"
        )
    
    duration = plan.get_duration(request.duration_days)
    if not duration:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Длительность {request.duration_days} дней недоступна для этого плана"
        )
    
    gateway = await payment_service.get_by_type(request.gateway_type)
    if not gateway or not gateway.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Платёжный шлюз недоступен"
        )
    
    currency = request.currency or gateway.currency
    price = duration.get_price(currency)
    pricing = pricing_service.calculate(user, price, currency)
    
    transaction_plan = PlanSnapshotDto.from_plan(plan, duration.days)
    
    try:
        result = await payment_service.create_payment(
            user=user,
            plan=transaction_plan,
            pricing=pricing,
            purchase_type=PurchaseType.RENEW,
            gateway_type=request.gateway_type,
        )
        
        return ApiResponse(
            success=True,
            data=PaymentResponse(
                payment_id=str(result.id),
                payment_url=result.url,
                amount=str(pricing.final_amount),
                currency=currency.value,
                gateway_type=request.gateway_type.value.lower(),
                status="pending",
                expires_at=result.expires_at
            )
        )
    except Exception as e:
        logger.exception(f"Ошибка создания платежа для продления: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Ошибка создания платежа: {str(e)}"
        )


@router.get("/payments/{payment_id}/status", response_model=ApiResponse[PaymentStatusResponse])
@inject
async def get_payment_status(
    payment_id: str,
    transaction_service: FromDishka[TransactionService],
    telegram_id: int = Depends(get_current_user_id),
    api_key_auth: str = Depends(verify_api_key),
):
    """Получение статуса платежа."""
    from uuid import UUID
    
    try:
        payment_uuid = UUID(payment_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Неверный формат payment_id"
        )
    
    transaction = await transaction_service.get(payment_uuid)
    
    if not transaction or transaction.user.telegram_id != telegram_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Платёж не найден"
        )
    
    subscription_data = None
    if transaction.subscription:
        subscription_data = {
            "id": transaction.subscription.id,
            "status": transaction.subscription.status.value,
            "expire_at": transaction.subscription.expire_at.isoformat(),
        }
    
    return ApiResponse(
        success=True,
        data=PaymentStatusResponse(
            payment_id=str(transaction.payment_id),
            status=transaction.status.value,
            amount=str(transaction.pricing.final_amount),
            currency=transaction.currency.value,
            created_at=transaction.created_at,
            completed_at=transaction.completed_at,
            subscription=subscription_data
        )
    )


@router.get("/users/me/transactions", response_model=ApiResponse[list[TransactionResponse]])
@inject
async def get_user_transactions(
    transaction_service: FromDishka[TransactionService],
    telegram_id: int = Depends(get_current_user_id),
    api_key_auth: str = Depends(verify_api_key),
):
    """Получение истории транзакций пользователя."""
    transactions = await transaction_service.get_by_user(telegram_id)
    
    result = []
    for txn in transactions:
        result.append(TransactionResponse(
            id=txn.id,
            payment_id=str(txn.payment_id),
            status=txn.status.value,
            amount=str(txn.pricing.final_amount),
            currency=txn.currency.value,
            gateway_type=txn.gateway_type.value,
            purchase_type=txn.purchase_type.value,
            is_test=txn.is_test,
            created_at=txn.created_at,
            completed_at=txn.completed_at,
            plan_name=txn.plan.name if txn.plan else None,
            plan_duration=txn.plan.duration if txn.plan else None
        ))
    
    return ApiResponse(success=True, data=result)


@router.get("/users/me/referrals", response_model=ApiResponse[ReferralStatsResponse])
@inject
async def get_referral_stats(
    user_service: FromDishka[UserService],
    referral_service: FromDishka[ReferralService],
    telegram_id: int = Depends(get_current_user_id),
    api_key_auth: str = Depends(verify_api_key),
):
    """Получение статистики реферальной программы пользователя."""
    user = await user_service.get(telegram_id)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Пользователь не найден"
        )
    
    referral_count = await referral_service.get_referral_count(telegram_id)
    reward_count = await referral_service.get_reward_count(telegram_id)
    total_points = await referral_service.get_total_rewards_amount(telegram_id, ReferralRewardType.POINTS)
    total_extra_days = await referral_service.get_total_rewards_amount(telegram_id, ReferralRewardType.EXTRA_DAYS)
    
    referral_link = await referral_service.get_ref_link(user.referral_code)
    
    return ApiResponse(
        success=True,
        data=ReferralStatsResponse(
            referral_count=referral_count,
            reward_count=reward_count,
            total_points=total_points,
            total_extra_days=total_extra_days,
            referral_link=referral_link
        )
    )


@router.get("/users/me/balance", response_model=ApiResponse[BalanceResponse])
@inject
async def get_user_balance(
    user_service: FromDishka[UserService],
    telegram_id: int = Depends(get_current_user_id),
    api_key_auth: str = Depends(verify_api_key),
):
    """Получение баланса пользователя (баллы и скидка)."""
    user = await user_service.get(telegram_id)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Пользователь не найден"
        )
    
    return ApiResponse(
        success=True,
        data=BalanceResponse(
            points=user.points,
            personal_discount=user.personal_discount
        )
    )
