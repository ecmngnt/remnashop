#!/usr/bin/env python3
"""
Запуск только API без Telegram бота.
Используется для тестирования Website API.
"""
import uvicorn
from dishka.integrations.fastapi import setup_dishka as setup_fastapi_dishka
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from src.api.endpoints import payments_router, remnawave_router, website_router
from src.api.endpoints.remnawave_public import router as remnawave_public_router
from src.core.config import AppConfig
from src.core.logger import setup_logger
from src.infrastructure.di import create_container


def create_api_only_app() -> FastAPI:
    """Создание FastAPI приложения без Telegram бота."""
    setup_logger()
    
    config = AppConfig.get()
    
    # Создаем FastAPI приложение без lifespan (без бота)
    app = FastAPI(
        title="Remnashop API",
        description="API для управления VPN подписками",
        version="1.0.0"
    )
    
    # Настройка CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Подключаем роутеры
    app.include_router(payments_router)
    app.include_router(remnawave_router)
    app.include_router(website_router)
    app.include_router(remnawave_public_router)
    
    # Настройка DI контейнера (без bg_manager_factory)
    container = create_container(config=config, bg_manager_factory=None)
    setup_fastapi_dishka(container=container, app=app)
    
    @app.get("/")
    async def root():
        return {
            "status": "ok",
            "message": "Remnashop API (без Telegram бота)",
            "docs": "/docs"
        }
    
    @app.get("/health")
    async def health():
        return {"status": "healthy"}
    
    return app


if __name__ == "__main__":
    config = AppConfig.get()
    
    print("=" * 60)
    print("🚀 Запуск Remnashop API (только API, без Telegram бота)")
    print("=" * 60)
    print(f"📡 API будет доступен на: http://{config.host}:{config.port}")
    print(f"📚 Документация: http://{config.host}:{config.port}/docs")
    print(f"🔑 Website API Key: {config.website.api_key.get_secret_value()}")
    print(f"🌐 CORS Origins: {config.origins}")
    print("=" * 60)
    
    uvicorn.run(
        "run_api_only:create_api_only_app",
        host=config.host,
        port=config.port,
        reload=True,
        factory=True,
    )
