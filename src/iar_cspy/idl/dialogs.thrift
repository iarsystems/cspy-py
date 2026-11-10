namespace cpp CSpy.Dialogs.Thrift
namespace java com.iar.frontend

include "shared.thrift"
include "listwindow.thrift"

const string DIALOG_SERVICE = "dialogs";
const shared.ServiceVersion DIALOG_VERSION = {
    "major": 1, // Increment for breaking changes
    "minor": 0  // Increment for non-breaking changes
};

/**
 * This is a small service able to evaluate current state of a dialog.
 */
service DialogService extends shared.HeartbeatService {
    /** Get the service version hosted by the server. */
    shared.ServiceVersion GetServiceVersion();

    bool SetValue(1: string itemId ,2: shared.PropertyTreeItem items);
    listwindow.ToolbarItemState GetState(1: string itemId);
}