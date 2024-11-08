# general api exception
class BrivoError(Exception):
    """General Brivo error"""

    pass


class BrivoApiError(BrivoError):
    """Brivo API erros"""

    pass


# specific exception to differently handle user not found
class BrivoUserNotFoundError(BrivoError):
    pass
