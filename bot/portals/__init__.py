from .linkedin import LinkedInPortal
from .indeed import IndeedPortal
from .computrabajo import ComputrabajoPortal
from .getonbrd import GetOnBrdPortal

PORTAL_REGISTRY = {
    "linkedin":     LinkedInPortal,
    "indeed":       IndeedPortal,
    "computrabajo": ComputrabajoPortal,
    "getonyboard":  GetOnBrdPortal,
}
