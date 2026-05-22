from __future__ import annotations

from abc import ABC, abstractmethod

from geoskillbench.models.result import ExecutorSession, ExecutorSessionRequest, ExecutorStepResult


class Executor(ABC):
    executor_type: str

    @abstractmethod
    def create_session(self, request: ExecutorSessionRequest) -> ExecutorSession:
        raise NotImplementedError

    @abstractmethod
    def send_message(self, session_id: str, message: str) -> ExecutorStepResult:
        raise NotImplementedError

    @abstractmethod
    def close_session(self, session_id: str) -> None:
        raise NotImplementedError
