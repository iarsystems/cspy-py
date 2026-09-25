// Thrift service definitions for the C-SPY debugger service and auxiliary services /////////

namespace cpp CSpy.Thrift
namespace java com.iar.cspy.debugger

include "shared.thrift"

/** The unique identifier for the Debugger service */
const string DEBUGGER_SERVICE = "debugger";
/** The unique identifier for the Debugger Event Handler service */
const string DEBUGEVENT_SERVICE = "debugger.eventhandler";
/** The unique identifier for the Context Manager service */
const string CONTEXT_MANAGER_SERVICE = "debugger.contextmanager";
/** The unique identifier for the Memory service */
const string MEMORY_SERVICE = "debugger.memory";

/**
 * Configuration information needed to start a debug session.
 * 
 * This contains all information necessary to start a debug session, including
 * information about which plugins to load, etc.
 *
 * This corresponds roughly to ICSpyConfiguration in Eclipse.
 */
struct SessionConfiguration
{
  1: string type,
  2: string driverName,
  3: string processorName,
  4: list<string> options,
  //5: list<string> extraOptions, // Removed from 1.1.7, only options are supported. See Jira IDE-4125 
  //6: bool usingExtraOptions,
  //7: list<string> internalOptions,
  8: string executable,
  9: string toolkitDir,
  10: string target,
  11: string projectName,
  12: string projectDir,
  14: shared.StackSettings stackSettings
  15: list<string> setupMacros,
  16: list<string> plugins,

  /** The name of the configuration we are launching, e.g. "Debug", "Release" */
  17: string configName,

  /** True if C-RUN should be enabled, false if not. */
  18: bool enableCRun;
  
  /** True if we should attach to a running target */
  19: bool attachToTarget;
  
  /** True if we leave the target running on debug session end (from 1.1.11) */
  20: bool leaveRunning;    
}

/**
 * The list of all notifications. This is copy-pasted from
 * DkNotifyApi.h.
 */
enum DkNotifyConstant
{
  /** The target system has stopped executing (all cores)*/ 
  kDkTargetStopped = 0,
  /** The target system has started executing (any core)*/ 
  kDkTargetStarted = 1,
  /** The target system has been reset*/ 
  kDkReset = 2,
  /** Memory (any memory) has changed*/ 
  kDkMemoryChanged = 3,
  /** The debugger has switched focus to a new context*/ 
  kDkInspectionContextChanged = 4,
  /** The debugger has switched focus to a new base context*/ 
  kDkBaseContextChanged = 5,
  /** The debugger is about to load a debug file*/ 
  kDkPreLoadModule = 6,
  /** The debugger has just finished loading a debug file*/ 
  kDkPostLoadModule = 7,
  /** The debugger has just finished loading a special prefix debug file*/ 
  kDkPostLoadPrefixModul = 8,
  /** User breakpoints have been modified*/ 
  kDkUserBreakUpdate = 9,
  /** The debugger has finished configuration*/ 
  kDkPostConfig = 10,
  /** The debugger is about to shut down*/ 
  kDkPreShutDown = 11,
  /** The debugger is shutting down*/ 
  kDkDoShutDown = 12,
  /** A fatal error has been detected*/ 
  kDkFatalError = 13,
  /** The driver has spontaneously reset*/ 
  kDkDriverReset = 14,
  /** Force an update of all debugger windows*/ 
  kDkForceUpdate = 15,
  /** Announce that the debugger state is about to change*/ 
  kDkPreModify = 16,
  /** This is only issued immediately before a kDkCNTargetStopped, to
      indicate that the system was "forced" to stop, such as by
      hitting a breakpoint, as opposed to stopping "naturally". such
      as by completing a step. */
  kDkForcedStop = 17,
  /** This is issued when the main thread requests stop of target
      execution.  It's only purpose is to allow client code which is
      occupied in an execution callback, such as a breakpoint handler,
      to return from the callback immediately. Typically, the client
      would not spend significant time in a callback, and wouldn't
      need to bother, but in very special circumstances the callback
      function might want to suspend itself/the execution thread. */
  kDkStopRequested = 18,
  /** As kDkFatalError, but the backend has already informed the user*/ 
  kDkSilentFatalError = 19,
  /** The debugger is about to shut down. This is sent before kDkPreShutDown*/ 
  kDkPrePreShutDown = 20,
  /** The debugger has just finished loading an additional debug file*/ 
  kDkPostLoadExtraModule = 21,
  /*! The kernel thread has just started executing. This is the first
      event triggered from the kernel thread. */
  kDkKernelThreadStarted = 22,
  /*! The kernel thread is about to terminate. This is the last event
      triggered from the kernel thread. */
  kDkKernelThreadExiting = 23,
  /*! Meta data (code coverage etc) has been changed */
  kDkMetaDataChanged = 24,
  /** A plugin is about to be loaded*/ 
  kDkPreLoadPlugin = 25,
  /** A plugin has been loaded*/ 
  kDkPostLoadPlugin = 26,
  /** A plugin is about to be unloaded*/ 
  kDkPreUnloadPlugin = 27,
  /** A plugin has been unloaded*/ 
  kDkPostUnloadPlugin = 28,
  /** A service has been added or removed*/ 
  kDkServicesChanged = 29,
  /** The target has finished static initialization*/ 
  kDkFinishedStaticInit = 30,
  /** A core in the target system has stopped executing*/ 
  kDkCoreStopped = 31,
  /** A core in the target system has started executing*/ 
  kDkCoreStarted = 32,
}

/**
 * Thrift-variant of DkCoreStatus.
 */
enum DkCoreStatusConstants
{
  kDkCoreStateStopped = 0,
  kDkCoreStateRunning = 1,
  kDkCoreStateSleeping = 2,
  kDkCoreStateUnknown = 3,
  kDkCoreStateNoPower = 4
}

/**
 * Sent to the debug-event listener service when a debug event
 * happens. See DkNotifyApi.h.
 */
struct DebugEvent
{
  1: DkNotifyConstant note;
  2: string descr;
  3: list<string> params; // additional parameters
}

/**
 * See kDkInspectionContextChanged. This is its own struct since we
 * want to send a context ref with it (with the generic DebugEvent
 * we can only send a description and string parameters).
 */
struct InspectionContextChangedEvent
{
  1: shared.ContextRef context;
}

/**
 * See kDkBaseContextChanged. Also, see comment above on 
 * why this is a separate struct.
 */
struct BaseContextChangedEvent
{
  1: shared.ContextRef context;
}

/**
 * See DkLoggingCategory in DkLogApi.h.
 * 
 * Note that the enum value should be relied upon. To convert to 
 * and from the DkLoggingCategory constants, use the convert() functions
 * in CSpyTypeConverters.h.
 */
enum DkLoggingCategoryConstant
{
  kDkLogUser = 0,
  kDkLogInfo = 1,
  kDkLogWarning = 2,
  kDkLogError = 3,
  kDkLogMinorInfo = 4,
}

/**
 * Sent to the debug event listener when a log event happens.
 */
struct LogEvent
{
  1: DkLoggingCategoryConstant cat,
  2: string text,
  3: i64 timestamp,
}

/**
 * Interface for receiving information about events in the debugger.
 */
service DebugEventListener
{
  /**
   * Called whenever a debug event happens. See DkNotifySubscriber#Notify.
   */
  oneway void postDebugEvent(1: DebugEvent event)

  /** 
   * This one should not be oneway, since we need to make sure that the
   * client has actually recevied the message before proceeding. This will
   * otherwise prevent e.g. fatal error messages from being seen.
   */
  void postLogEvent(1: LogEvent event)

  /**
   * Triggered on kDkInspectionContextChanged.
   */
  oneway void postInspectionContextChangedEvent(1: InspectionContextChangedEvent event);

  /**
   * Triggered on kDkBaseContextChanged.
   */
  oneway void postBaseContextChangedEvent(1: BaseContextChangedEvent event);
}

/**
 * Information about a thread (or an RTOS task).
 */
struct Thread
{
  1: i32 id
  2: string name
}

/**
 * This is used by the UI to determine how to present the value of
 * an expression. It does not, for example, provide enough information in
 * order to be able to evaluate an expression.
 */
enum BasicExprType
{
  Unknown,
  Basic,
  Pointer,
  Array,
  Composite,
  Enumeration,
  Function,
  Custom
}

/*
 * This is the result of evaluating an expression.
 */
struct ExprValue
{
  /** The expression which was evaluated*/ 
  1: string expression;

  /** The resulting value.*/ 
  2: string value;

  /** The type of the value, suitable to use as a label in a UI.*/ 
  3: string type;

  /** Indicates if this is an L-value or not, i.e. if it can be
      used on the left-hand sign in an assignment.
   */
  4: bool isLValue;

  /** True if this expression has a location (i.e. if it resides in
     such a way that it can be accessed using a location).
    */
  5: bool hasLocation;

  /** The location of the expression, if any. */
  6: shared.Location location;

  /** The number of sub-expressions. Will be 0 for scalar types,
     but non-null for e.g. arrays and structs.
    */
  7: i32 subExprCount;

  /** The basic type of the expression. Used for determining UI aspects
     of how to render the result (e.g. icons).
    */
  8: BasicExprType basicType;

  /** The size of the expression in bytes.*/
  9: i32 size;
}

/** 
 * Information about a loaded module.
 */
struct ModuleData 
{
  /** The name of the module. */
  1: string name

  /** The file from which the module was loaded. */
  2: string file
  
  3: i64 timestamp
  4: string baseAddress
  5: string toAddress
  6: bool symbolsAreLoaded
  7: i64 size
}

struct NamedLocationMask
{
  1: bool used
  2: i32 shift
  3: i64 mask
}

struct NamedLocation
{
  1: string name
  2: string nameAlias # called "nameAlias" since "alias" is reserved
  # 3: list<NamedLocation> bitfields
  4: bool readonly
  5: bool writeonly
  6: shared.Location location
  7: shared.Location realLocation
  8: i16 valueBitSize
  9: i16 fullBitSize
  10: i16 defaultBase
  11: bool usesMask
  12: list<NamedLocationMask> masks
  13: string description
  # 14: list<string> groups
}

struct ExtraDebugFile
{
  1: bool doDownload
  2: string path
  3: i64 offset
}

struct ModuleLoadingOptions
{
  1: bool resetAfterLoad
  2: bool callUserMacros
  3: bool onlyPrefixNotation
  4: bool suppressDownload
  5: bool shouldAttach
  6: bool shouldLeaveRunning
  7: list<ExtraDebugFile> extraDebugFiles
}

struct ResetStyles
{
  1:string name
  2:i32 id
  3:bool selected
  4:string tooltip	
}

struct DebugSettings
{
  1:bool alwaysPickAllInstances
  2:bool enterFunctionsWithoutSource
  3:i32  stlDepth
  4:i32  staticWatchUpdateInterval
  5:i32  memoryWindowUpdateInterval
  6:i32  globalIntegerFormat    
} 

/**
 * Main C-SPY service. 
 */
service Debugger extends shared.HeartbeatService
{
  /** Returns the version of the debugger as a string. */
  string getVersionString();
  
  /** Starts a debug session. Normally the very first thing which is
     invoked in the lifecycle of a debug session. Responsible to
     prepare the debugger to be able to load a module.

     @deprecated Use configureSession() instead.
    */
  void startSession(1: SessionConfiguration sessionConfig) throws (1: shared.CSpyException e);

  /** Resolves a launch configuration from a json represented as a string.
     @todo refer to a json schema.
    */
  shared.LaunchConfiguration resolveLaunchConfiguration(1: string launchJson) throws (1: shared.CSpyException e);

  /** Configures a debug session based on the specified launch configuration.
      This function is typically the first to be invoked in the debug session lifecycle,
      preparing the debugger for initialization.
    */
  void configureSession(1: shared.LaunchConfiguration config) throws (1: shared.CSpyException e);

  /** Runs the standard debugger initialization sequence based on the configured launch configuration.
     This sequence includes module loading, flashing, and macro loading, eliminating the need to
     perform these steps separately.

     @note configureSession() must be called before invoking this function.
  */
  void startSMPSession() throws (1: shared.CSpyException e);

  /** Stops the debug session. When this method returns, the event
     kDkDoShutdown should have been triggered.
     
     @deprecated This method should not be called by clients directly. Use <tt>exit()</tt> instead.
    */
  void stopSession() throws (1: shared.CSpyException e);

  /** Get user settings from the EW */
  DebugSettings getDebugSettings() throws (1: shared.CSpyException e);

  /** Set user settings of the debugger session. */
  void setDebugSettings(1: DebugSettings settings) throws (1: shared.CSpyException e);
  
  /** Cause CSpyServer to terminate. Will call stopSession() if necessary.
     <p>Note: we currently do not support calling startSession() again after
     stopSession(), e.g. there can only be a single "start - stop - exit" cycle
     per process. This is due to limitations in the semantics of dlclose()
     on POSIX system. stopSession() is thus deprecated and should not be called
     directly from clients.
    */
  void exit() throws (1: shared.CSpyException e);
  
  /** Returns true if the debugger is "online", i.e. has a program loaded
     and can be expected to receive commands. This is roughly defined as
     between kDkLoadModule and kDkDoShutdown. Note that this can only be
     an approximation, as the state of the debugger may change between
     checking this flag and acting on it. It should only be used to provide
     hints to the user such as UI element state.
    */
  bool isOnline() throws (1: shared.CSpyException e); 

  /** Loads a module. See loadModuleWithOptions.*/ 
  void loadModule(1: string filename) throws (1: shared.CSpyException e);

  /** Loads a module with additional loading options.*/ 
  void loadModuleWithOptions(1: string filename, 2: ModuleLoadingOptions options) throws (1: shared.CSpyException e)

  /** Performs flashloading. 
     <p>TODO flashloading should be hidden behind loadModule().
     TODO the executable and arguments parameters are unused
     (leftovers from the old CDP-based debugger API?)  
    */
  void flashModule(1: string boardFile, 2: string executable, 3: list<string> argument_list, 4: list<string> extraExecutables) throws (1: shared.CSpyException e);

  /** Returns a list of the passes*/ 
  list<list<string>> getFlashPasses(1: string boardFile) throws (1: shared.CSpyException e)

  /** Erase the flash memory*/ 
  void eraseFlash(1: string boardFile, 2: list<bool> nPasses) throws (1: shared.CSpyException e);

  /** Return a list of all loaded modules*/ 
  list<ModuleData> getModules() throws (1: shared.CSpyException e);

  /** Loads a macrofile.*/ 
  void loadMacroFile(1: string macro) throws (1: shared.CSpyException e);

  /** Unload a macrofile*/ 
  void unloadMacroFile(1: string macro) throws (1: shared.CSpyException e);

  void runToULE(1: string ule, 2: bool allowSingleStep) throws (1: shared.CSpyException e);

  /** Get the current state of the multicore settings.*/ 
  i64 getMulticoreFlags() throws (1: shared.CSpyException e);

 
  /*
   * RTOS stuff to be moved to RTOS service
   */
  
  /** Return a list of available "threads". These may or may not be
     actual threads. They correspond roughly to available
     "base contexts", or "threads" as present in the Eclipse debug view.
     TODO move to RTOS service
    */
  list<Thread> getThreadList() throws (1: shared.CSpyException e);

  /** Check if the given thread is the active thread or not.*/ 
  bool isActiveThread(1: Thread t) throws (1: shared.CSpyException e);


  /*
  * Expressions (TODO Move to expressions service)
  */

  /**  
   *  Each expression has a root expression which is always a string. Subexpressions are then referred to as a list of indexes 
   * (instead of the perhaps more intuitive list of subexpression names).
   *
   * <tt>   
   *   struct A {
   *       int x;
   *       int y;
   *   };
   *
   *   struct B {
   *     struct A a[10];
   *   };
   *
   *   struct B b;
   * </tt>
   *
   * In this example, the expression b.a[5].y would be represented using rootExpr = "b", and subExprIndex = [0, 5, 1], 
   * where 0 is the index of a (since it is the first member of struct B), 5 is the array index, and 1 is the index of y 
   * which is the second member of struct A.
   *   
   * When evaluating expressions, you pass the root expression and the list of indexes to obtain a ExprValue object, 
   * which contains the value of the expression (as a string, according to the requested format). Also the ExprValue object 
   * will tell you if there are any subexpressions and if so, how many.
   *
   * If prefix is true, the formatted value will be given a prefix appropriate
   * the specified format.
   *
   */
  ExprValue evalExpression(1: shared.ContextRef ref, 2: string expr, 3: list<i32> subExprIndex, 4: shared.ExprFormat format, 5: bool prefix) throws (1: shared.CSpyException e)
  
  void assignExpression(1: shared.ContextRef ref, 2: string expr, 3: list<i32> subExprIndex, 4: ExprValue rvalue) throws (1: shared.CSpyException e);
    
  /** Returns a list of sub-expression labels.
     Labels are for display to the user, and may be created by custom data structure
     visualisations introduced via custom_formats.dat
    
     treatPointerAsArray is a new field used to implement support for "Display as array",
     see ECL-2592.
    */
  list<string> getSubExpressionLabels(1: shared.ContextRef ref, 2: string rootExpr, 3: list<i32> subExprIndex, 4: i32 startIndex, 5: i32 length, 6: bool treatPointerAsArray) throws (1: shared.CSpyException e);

  /*
   * Symbols and locations
   */
  /** Return a list of all location names (e.g. registers)*/ 
  list<string> getLocationNames() throws (1: shared.CSpyException e);
  
  /** Return a list of all location names in the given group*/ 
  list<string> getLocationNamesInGroup(1: string group) throws (1: shared.CSpyException e);
  
  /** Return a list of all register groups*/ 
  list<string> getRegisterGroups() throws (1: shared.CSpyException e);
  
  /** Return information about a specific named location
     Throws exception if no such location could be found.
    */
  NamedLocation getNamedLocation(1: string name) throws (1: shared.CSpyException e);

  /* 
   * Run-control. 
   */

  DkCoreStatusConstants getCoreState(1: i32 core) throws (1: shared.CSpyException e);
  
  i32 getNumberOfCores() throws (1: shared.CSpyException e);
  
  string getCoreDescription(1: i32 core);
  
  i64 getCycleCounter(1: i32 core) throws (1: shared.CSpyException e);
  
  i64 getCyclesPerSecond() throws (1: shared.CSpyException e);
  
  bool hasCoreStoppedDeliberately(1: i32 core) throws (1: shared.CSpyException e);

  void setResetStyles(1: i32 id) throws (1: shared.CSpyException e);

  list<ResetStyles> getResetStyles() throws (1: shared.CSpyException e);
  
  void reset() throws (1: shared.CSpyException e);
  
  void go() throws (1: shared.CSpyException e);

  void goCore(1: i32 core) throws (1: shared.CSpyException e);
  
  void stop() throws (1: shared.CSpyException e);
  
  void stopCore(1: i32 core) throws (1: shared.CSpyException e); 
  
  void multiGo(1: i32 core) throws (1: shared.CSpyException e);
  
  void step(1: bool enterFunctionsWithoutSource) throws (1: shared.CSpyException e);
  
  void stepOver(1: bool enterFunctionsWithoutSource) throws (1: shared.CSpyException e);
  
  void nextStatement(1: bool enterFunctionsWithoutSource) throws (1: shared.CSpyException e);
  
  void stepOut() throws (1: shared.CSpyException e);
 
  void instructionStep() throws (1: shared.CSpyException e);
  
  void instructionStepOver() throws (1: shared.CSpyException e);
  
  void goToLocation(1: shared.Location location) throws (1: shared.CSpyException e);
  
  void goToLocations(1: list<shared.Location> locations) throws (1: shared.CSpyException e);

  bool supportsExceptions() throws (1: shared.CSpyException e);

  bool getBreakOnThrow() throws (1: shared.CSpyException e);

  void setBreakOnThrow(1: bool enable) throws (1: shared.CSpyException e);

  bool getBreakOnUncaughtException() throws (1: shared.CSpyException e);

  void setBreakOnUncaughtException(1: bool enable) throws (1: shared.CSpyException e);
    
  shared.ZoneInfo getZoneByName(1: string name) throws (1: shared.CSpyException e);

  shared.ZoneInfo getZoneById(1: i32 id) throws (1: shared.CSpyException e);

  list<shared.ZoneInfo> getAllZones() throws (1: shared.CSpyException e);

  /** Returns the current trace timestamp used by the IfTraceUtil.cpp
     trace functions. This is used to allow e.g. Eclipse to synchronize
     trace streams.
    */
  i64 getTraceTime();
}

/**
 * Handles contexts and call stack functionality.
 */
service ContextManager extends shared.HeartbeatService
{
  /** Set the current inspection context. This is invoked for example when
     the user selects a stack frame in the Eclipse debug view.
    */
  void setInspectionContext(1: shared.ContextRef context);

  /** Try to locate the given context. This can be used to e.g. find
     what the "current inspection context" is looking at. This works
     by first converting the incoming context ref into a DkContext,
     and then convert that DkContext back into a context ref. This
     means that if e.g. the current inspection context does not
     refer to any known context, this method will return a "Unknown"
     context.

     <p>NOTE: The C-SPY implementation of this method is fundamentally broken,
     and calling this method will cause an unconditional exception to
     be thrown. See ECL-2260 for more info. /JesperEs 2018-04-18
  */
  shared.ContextRef findContext(1: shared.ContextRef context);
  
  /** Get the call stack of the given context.*/ 
  list<shared.ContextInfo> getStack(1: shared.ContextRef context, 2: i32 low, 3: i32 high);
  
  i32 getStackDepth(1: shared.ContextRef context, 2: i32 maxDepth);
  
  /** Return information about a context, such as source ranges and function name.*/ 
  shared.ContextInfo getContextInfo(1: shared.ContextRef context);

  /** Compare two context refs for equality.*/ 
  bool compareContexts(1: shared.ContextRef ctx1, 2: shared.ContextRef ctx2);

  list<shared.Symbol> getLocals(1: shared.ContextRef ctx);

  list<shared.Symbol> getParameters(1: shared.ContextRef ctx);

  bool isExecuting(1: shared.ContextRef ctx);
  
  void setExecLocation(1: shared.ContextRef ctx, 2: string ule) throws (1: shared.CSpyException e);
}
