namespace cpp CRun.Thrift
namespace java com.iar.crun

include "ServiceRegistry.thrift"
include "shared.thrift"

const string CRUN_DISPLAY_SERVICE = "crun.display";
const string CRUN_BACKEND_SERVICE = "crun.backend";

enum CRunBreakAction
{
  kStopAndLog,
  kLog,
  kIgnore
}

struct CRunMessage
{
  1: i32 id;
  2: i32 index;
  3: i32 core;
  4: string name;
  5: string text;
  6: i64 cycle;
  7: i32 repeatCount;
  8: list<CRunMessage> subMessages;
  9: list<string> callStack;
  10: bool noStop;
  11: shared.Location runTo;
  12: shared.Location userProgramCounter;
  13: list<shared.SourceRange> extraSourceRanges;
  14: shared.SourceRange pcSourceRange;
  15: string tooltip;
  16: CRunBreakAction breakAction;
}

/**
 * Service for presenting C-RUN messages to the user.
 */
service CRunDisplay
{
  void itemAdded(1: CRunMessage message, 2: string partnerNamespace);

  void itemUpdated(1: CRunMessage message, 2: string partnerNamespace);
  
  void itemRemoved(1: i32 index, 2: string partnerNamespace);
  
  void updateAll(1: string partnerNamespace);
  
  void filtersChanged(1: string partnerNamespace);
}


/**
 * Service for controlling filters and actions.
 */
service CRunBackend
{
  void loadFilters(1: string filename);

  void saveFilters(1: string filename);
  
  void addRuleOnMessage(1: string messageName);

  void addRuleOnMessageFile(1: string messageName, 2: string file);

  void addRuleOnMessageRange(1: string messageName, 2: shared.SourceRange range);

  void setDefaultAction(1: CRunBreakAction action);
  
  CRunBreakAction getDefaultAction();
  
}
