"""Only the two owner-invoked manual strategy workflows may write."""
from scheduler.production_session import context

def authorize(env, requester, reader_factory):
    context(env)
