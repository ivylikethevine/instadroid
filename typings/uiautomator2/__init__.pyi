# Local type stubs for the slice of uiautomator2 this project uses (the package ships no type
# information). The device connect() returns is declared once, as instadroid.uidevice's structural
# Device; the runtime class has much more.
from instadroid.uidevice import Device

def connect(serial: str | None = None) -> Device: ...
