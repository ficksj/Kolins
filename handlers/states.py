from aiogram.fsm.state import State, StatesGroup


class UserSearchState(StatesGroup):
    waiting_for_query = State()


class PCSearchState(StatesGroup):
    waiting_for_query = State()
