namespace java com.iar.cspy
namespace cpp Shared.Thrift

/**
 * See DcResult in CoreUtil/include/DcCommon.h
 */
enum DcResultConstant
{
  kDcOk,
  kDcRequestedStop,
  kDcOtherStop,
  kDcUnconditionalStop,
  kDcSympatheticStop,
  kDcBusy,
  kDcError,
  kDcFatalError,
  kDcLicenseViolation,
  kDcSilentFatalError,
  kDcFailure,
  kDcDllLoadLibFailed,
  kDcDllFuncNotFound,
  kDcDllFuncSlotEmpty,
  kDcDllVersionMismatch,
  kDcUnavailable
}

/**
 * General exception throw when things go wrong in the debugger.
 */
exception CSpyException
{
  1: DcResultConstant code;

  // The method which returned the DcResult code, if any.
  2: string method;

  // An explanatory message.
  3: string message;

  // The reason for the error (e.g. an unparsable expression)
  4: string culprit;
}

struct Id
{
    1: string value;
    2: string type;
}

struct Success
{
    1: bool value;
    2: string failureMessage;
}

struct Zone
{
  1: i32 id
}

struct ZoneInfo
{
  1: i32 id
  2: string name
  3: i64 minAddress
  4: i64 maxAddress
  5: bool isRegular
  6: bool isVisible
  7: bool isBigEndian
  8: i32 bitsPerUnit
  9: i32 bytesPerUnit
}

struct Location 
{
  1: Zone zone,
  2: i64 address
}

struct SourceLocation
{
  1: string filename;
  // TODO Document whether these are start from 0 or 1
  2: i32 line;
  3: i32 col;
  4: list<Location> locations;
}

struct SourceRange
{
  1: string filename;
  2: SourceLocation first;
  3: SourceLocation last;
  4: string text;
}

struct Symbol
{
  1: string name;
}

// Must match DkValue::Format.
enum ExprFormat
{
  kDefault = 0,
  kBin = 1,
  kOct = 2,
  kDec = 3,
  kHex = 4,
  kChar = 5,
  kStr = 6,
  kNoCustom = 7
}

enum ContextType
{
  // The current base context.
  CurrentBase,

  // The current inspection context.
  CurrentInspection,

  // A frame on the current stack. If level == 0, this is identical
  // to the current base context.
  Stack,

  // The target context. The core is specified by the "core" field in
  // the ContextRef struct.
  Target,

  // The task context. The task id is specified by the "task" field in
  // the Contextref struct.
  Task,

  // An unknown context.
  Unknown,
}

/**
 * A context ref is a way to refer to, or address, a context. Context refs
 * are primarily sent from the UI to the debugger when performing operations
 * which require a context. The debugger will then have to obtain a DkContext
 * object explicitly using the kernel client API.
 */
struct ContextRef
{
  // What type of context this is.
  1: ContextType type;

  // Stack level. Valid only if type == Stack.
  2: i32 level;

  // Core number. Valid only if type == Target.
  3: i32 core;

  // Task id. Valid only if type == Task.
  4: i32 task;
}

/*
 * Additional information about a context. This is only sent *from* the 
 * debugger, never to it.
 */
struct ContextInfo
{
  1: ContextRef context;

  // Other ref:s which are equivalent ways of obtaining this context.
  5: ContextRef aliases;
  
  // Source ranges associated with this context
  2: list<SourceRange> sourceRanges;

  3: Location execLocation;
  
  4: string functionName;
}

/*
 * Base class for services which have heartbeat support. This can
 * be used by clients to check if the service is still alive.
 */
service HeartbeatService
{
  // If this method returns, the service is alive. If the service
  // is dead, some form of exception will happen.
  void isAlive();
}

/**
 * Settings needed for the stack view. These needs to be part of the
 * initial debugger configuration, and cannot wait until the stack view
 * is shown.
 */
struct StackSettings  
{
  1: bool fillEnabled;
  2: bool overflowWarningsEnabled;
  3: bool spWarningsEnabled;
  4: bool warnLogOnly;
  5: i32 warningThreshold;
  6: bool useTrigger;
  7: string triggerName;
  8: bool limitDisplay;
  9: i32 displayLimit;
}

/**
 * Breakpoint access types.
 */
enum AccessType
{
  kDkFetchAccess = 1,
  kDkReadAccess = 2,
  kDkWriteAccess = 3,
  kDkReadWriteAccess = 4
}

/**
 * Information about a breakpoint.
 */
struct Breakpoint
{
  1: i32 id;
  2: string ule;
  3: string category;
  4: string descriptor;
  5: string description;
  6: bool enabled;
  7: bool isUleBased;
  8: AccessType accessType;
  9: bool valid;
  
}

struct PropertyTreeItem
{
  1: string key,
  2: string value,
  3: list<PropertyTreeItem> children
}

struct Capabilities
{
  1: bool supportsEditorHighlight,
}

struct ExtraImage {
  1: string image,
  2: i64    offset,
  3: bool   suppressDownload,
}

struct DownloadConfiguration {
  1: string flashLoader,
  2: list<string> deviceMacros,
  3: bool suppressAllDownloads,
  4: bool suppressProgramDownload,
  5: bool performMassErase,
  6: list<ExtraImage> extraImages,
  7: bool verifyAllDownloads,
}

struct LaunchConfiguration {
  1: string program,
  2: list<string> programArgs,
  3: string targetOrEmpty,
  4: string processor,
  5: string driverNameOrEmpty,
  6: string driverLib,
  7: list<string> driverOptions,
  8: list<string> setupMacros,
  9: map<string, string> macroParams,
  10: list<string> plugins,
  // The last part of the project path, e.g. MyProject.ewp.
  // Note: this may be empty in cases where there's no project, such as when using CSpyBat
  11: string projectFilenameOrEmpty,
  12: string projectDirOrEmpty,
  13: string configNameOrEmpty,
  14: bool attachToTarget,
  15: bool handleCRunEvents,
  16: bool leaveTargetRunning,
  17: string stopOnSymbol,
  18: DownloadConfiguration download,
}
