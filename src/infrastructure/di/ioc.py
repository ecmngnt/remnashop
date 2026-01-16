from typing import Optional

from aiogram_dialog import BgManagerFactory
from dishka import AsyncContainer, make_async_container

from src.core.config import AppConfig

from .providers import get_providers


def create_container(
    config: AppConfig, 
    bg_manager_factory: Optional[BgManagerFactory] = None
) -> AsyncContainer:
    context = {
        AppConfig: config,
    }
    
    if bg_manager_factory is not None:
        context[BgManagerFactory] = bg_manager_factory

    container = make_async_container(*get_providers(), context=context)
    return container
