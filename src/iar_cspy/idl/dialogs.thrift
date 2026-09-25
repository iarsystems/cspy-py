namespace cpp CSpy.Dialogs.Thrift
namespace java com.iar.frontend

include "shared.thrift"
include "listwindow.thrift"

const string DIALOG_SERVICE = "dialogs";

/**
 * This is a small service able to evaluate current state of a dialog.
 */
service DialogService extends shared.HeartbeatService {
    bool SetValue(1: string itemId ,2: shared.PropertyTreeItem items);
    listwindow.ToolbarItemState GetState(1: string itemId);
}