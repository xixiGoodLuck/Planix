from fastapi import HTTPException


def bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"message": message})


def not_found(message: str = "Resource not found") -> HTTPException:
    return HTTPException(status_code=404, detail={"message": message})
