from .booking import Booking, BookingCreate, BookingForReview, BookingUpdate
from .invite import Invite
from .override import WorkdayOverride, WorkdayOverrideCreate, WorkdayOverrideUpdate
from .subscription import Subscription
from .users import (
    Client,
    ClientCreate,
    ClientDetails,
    ClientUpdate,
    Master,
    MasterCreate,
    MasterUpdate,
    MasterWithClients,
    MasterWithOverrides,
)

ClientDetails.model_rebuild(_types_namespace={"Booking": Booking})
# BookingForReview.model_rebuild(_types_namespace={"Master": Master})
# BookingForReview.model_rebuild(_types_namespace={"Client": Client})
