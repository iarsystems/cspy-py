namespace cpp AmpSync.RPC
namespace java com.iar.cspy.amp //?
include "shared.thrift"
include "cspy.thrift" 
include "ServiceRegistry.thrift"

//Verions of AMP. Only clients with the same version are allowed to connect.
const i32 AMP_VERSION = 1

const string CORES_REGISTRY_SERVICE = "com.iar.cspy.ampsync.cores_registry"
const string PARTNER_REGISTRY_SERVICE = "com.iar.cspy.ampsync.partner_registry"
const string EVENT_BUS_SERVICE = "com.iar.cspy.ampsync.eventbus"
const string SESSION_SYNCHRONIZER_SERVICE = "com.iar.cspy.ampsync.session_synchronizer"
const string GATE_KEEPER_SERVICE  = "com.iar.cspy.ampsync.gate_keeper"
const string MULTI_CORE_LOG_SERVICE = "com.iar.cspy.ampsync.multi_core_log"
const string SOFT_CTI_SERVICE = "com.iar.cspy.ampsync.soft_cti"
const string CPU_STATUS_POLL_CONTROL_SERVICE = "com.iar.cspy.ampsync.cpu_status_poll_control"
const string DEBUGGER_STARTER_SERVICE="com.iar.cspy.ampsync.debugger_starter"


//=============================================================================
//! Contains key/value pairs for configuration of the session
// For the EW this includes ewp file path, configuration name etc.
// For headless debugging this contains debugee binary
// For all of them it contains flags like attachToRunningTarget,
typedef map<string, string> PartnerSessionConfiguration;
typedef i32 PartnerId;
//Cores are numbered according to the CoresRegistry. A partners cores are indexed from b to b + n - 1
typedef i32 CoreId;

struct PartnerInformation {
	1: required PartnerId id;
	2: required cspy.SessionConfiguration cspyConfiguration;
	3: required PartnerSessionConfiguration configuration;
	4: required bool isAlive;
}

struct CoreInfo {
	1: required CoreId id;
	2: string name;
	3: cspy.DkCoreStatusConstants status;
	4: string statusString;
	5: string pc;
	6: string cc;
	7: PartnerId partner;
	8: i32 localCoreId;
	
}

//=============================================================================
const string PARTNER_REGISTRY_PARTNER_SET_EVENT = "partner_set.partner_registry"
const string PARTNER_REGISTRY_PARTNER_IS_ALIVE_EVENT = "partner_alive.partner_registry"
const string PARTNER_REGISTRY_PARTNER_IS_DECEASED_EVENT = "partner_deceased.partner_registry"
const string PARTNER_REGISTRY_EVENT_PARTNER_ID = "partnerId"
exception UnknownPartner {
	 1: required PartnerId id
}

//! Provides information about this debugging session and its participants
//Fires events on the event bus for AcknowledgePartnerIsAlive and SetPartnerInfo
service PartnerRegistry extends shared.HeartbeatService {
	list<PartnerInformation> GetAllPartners();
	PartnerInformation GetPartnerInfo(1: required PartnerId who) throws (1: UnknownPartner partner);
	//!When a partner comes up, it should call this to let the worl know its alive
	//The alive status is not cleared by subsequent SetPartner or SetAllPartners.
	void AcknowledgePartnerIsAlive(1: required PartnerId myId) throws (1: UnknownPartner partner);
	bool IsPartnerAlive(1: required PartnerId who) throws (1: UnknownPartner partner);
	
	//Uncertain of how to inject info here. but: 
	void SetPartnerInfo(1: required PartnerInformation information);
	void SetAllPartners(1: required list<PartnerInformation> allPartners);
}


//=============================================================================
//!Describes a range of cores, e.g for telling which cores belongs to a partner
// [first, last)
struct CoreRange {
	1: required CoreId first
	2: required CoreId last
}

//!topic for CoresRegistry.SetInfo()
const string CORES_REGISTRY_UPDATED_EVENT = "core_updated.cores_registry"

//!Provides information about the all cores in a session 
service CoresRegistry extends shared.HeartbeatService {
	i32 NumberOfCores();
	list<CoreInfo> AllCores();
	CoreInfo GetInfo(1: CoreId coreNumber) throws (1: UnknownPartner partner);
	//!things that are not set in updated are kept from the previous value. 
	oneway void SetInfo(1: CoreInfo updated);
	//!the isset flags does not survive through serialization, so here is a variant that uses the
	//same properties map as CORES_REGISTRY_UPDATED_EVENT
	oneway void SetInfoPartial(1: required CoreId updated, 2: required EventProperties props)
	
	//!returns the first coreId for the given partner
	CoreRange PartnerCores(1: PartnerId partner) throws (1: UnknownPartner partner); 
	
	CoreRange SetNumberOfCores(1: PartnerId partner, 2: i32 numberOfCores) throws (1: UnknownPartner partner)
	//!Called when one debug-session is over and we should be prepared to start over. Clears all cores.
	void ResetAfterDebugSession()
}

//=============================================================================
//For broadcasting events to all partners. 
// the Event::type is used to tag what event it is, it may be a good idea to 
// use reverese namespace notation to provide quicker checking 
// (foo.partnerregistry.ampsync instead of ampsync.partnerregistry.foo)

typedef map<string, string> EventProperties
struct Event {
	1: required string type;
	2: optional EventProperties properties;
}

const string EVENT_SENDING_PARTNERID_KEY = "sendingPartner";

exception EventBusRegistrationFailed {
	1: string message;
	2: i32 type; //From TTransportExceptionType
}

//!The event bus provides event handling for the amp session.
//One idea is to use http://joelpm.com/2009/04/03/thrift-bidirectional-async-rpc.html
//to handle distributing the events to connected clients. Later though,
service EventBus extends shared.HeartbeatService {
	oneway void Fire(1: Event toBeFired);
	
	//Until we have BidiThrift implemented, clients have to register them selves to the master server. This is also used to poll for partner aliveness.
	//Registering with an empy clientLocation means de-registration
	void RegisterWithVersion(1: ServiceRegistry.ServiceLocation clientLocation, 2: PartnerId myId, 3: i32 ampVersion) throws (1:EventBusRegistrationFailed regFaild)
	//Deprecated will always fail.	
	void Register(1: ServiceRegistry.ServiceLocation clientLocation, 2: PartnerId myId) throws (1:EventBusRegistrationFailed regFaild) 

}

//=============================================================================

enum SessionState
{
	Offline,
	SettingConfig,
	Building,
	BuildDone,
	StartFlashing,
	DoneFlashing,
	StartLoading,
	DoneLoading,
	StartAllLoaded,
	DoneAllLoaded,
	Running,
	EndSession,
	StartPartnerRegistryReset,
	DonePartnerRegistryReset,
	Terminate,
	RestartAtOffline,
	Disconnect,
}

//!Fired when a partner calls SyncTo(). When this is fired, the partner is already marked as in the dest state (e.g. in AllStates())
const string SESSION_SYNCHRONIZER_SYNCSTART_EVENT = "ampsync.session.syncstart"
//!key name for the state that is being left
const string SESSION_SYNCHRONIZER_FROM_KEY= "from"
//!kay name for the state being entered
const string SESSION_SYNCHRONIZER_TO_KEY = "to"
//!key name for the partner id of the partner that starts to wait.
const string SESSION_SYNCHRONIZER_SYNCSTART_SENDING_PARTNER_KEY = "waitingPartner"
//!Fired when all of the partners have SyncTo()ed to the same state.
const string SESSION_SYNCHRONIZER_SYNCCOMMIT_EVENT = "ampsync.sesssion.synccommit"

//!*****************
const string SESSION_SYNCHRONIZER_DEBUGGERSTART_EVENT = "ampsync.session.debuggerstart"
const string SESSION_SYNCHRONIZER_DEBUGGERENDSESSION_EVENT = "ampsync.session.debuggerendsession"
const string SESSION_SYNCHRONIZER_DEBUGGERTERMINATE_EVENT = "ampsync.session.debuggerterminate"
const string SESSION_SYNCHRONIZER_DEBUGGERDISCONNECT_EVENT= "ampsync.session.debuggerdisconnect"

const string SESSION_SYNCHRONIZER_EVENT_PARTNER_ID = "partnerId"

//!Handles synchronization of the partners so as to 
service SessionSynchronizer extends shared.HeartbeatService {
	SessionState SyncTo(1: PartnerId who, 2:SessionState destState)
	SessionState CurrentState(1: PartnerId who)
	list<SessionState> AllStates()
}

//=============================================================================
struct NumberOfCoresInfo {
	1: required i32 numberOfCores
	2: required i32 localCores
	3: required i32 localCoreOffset
}

// What should LowLevelGo do?
enum LowLevelAction {
	kDontCallLowLevelGo,
	kStartCPU,
	kStartAllCPUs,
	kPollingOnly
}

// What's the result of LowLevelGo and associated bp processing
enum CoreLowLevelResult {
	kFullStop,        // e.g. unconditional user breakpoint
    kReportToKernel,  // Kernel-requested breakpoint or stop
    kSympathetic,     // Stopped "by" other core
    kGoAgain,         // e.g. user breakpoint with false condition
    kTurnedZombie,    // became "disabled" while running
    kError,           // an error was returned by LLG (probably a zombie core)
    kIllegal
}

// What should happen after LowLevelGo
enum WhatNext {
	kReturnFromDriverGo,
	kLowLevelGoAgain,
	kReturnError,
	kReturnFromDriverGoAndStop
}

enum CoreStatus {
	kCoreStopped,
	kCoreRunning,
	kCoreZombie   // The core is unresponsive (for whatever reason)
}

service Gatekeeper extends shared.HeartbeatService {
  // Setup and initialization
  void SetRunAllCores(1: bool all)
  void SetStartOneStartsAll(1: bool on)
  void SetSoftCTI(1: bool on)

  // Call this before calling LowLevelGo(). May wait. Returns false if
  // we should return from Go().
  LowLevelAction BeforeLowLevelGo(1: CoreId core, 2: bool multi);
  
  // The thread which calls LowLevelGo to start one or all CPUs must
  // call this AFTER starting the CPU(s). Threads intending to poll only
  // will wait for this.
  void StartedCPU(1:CoreId core);
  
  //Called from DkNotification on kDkCoreStopped
  void CPUStoped(1: CoreId core);

  bool IsTargetStopped();

  // Call this after LowLevelGo() and associated bp processing. May
  // wait.
  WhatNext AfterLowLevelGo(1: CoreId core, 2:CoreLowLevelResult code);

  // TBD I guess we must also be aware of resets here.
  void Reset();

  // TBD how this should work 
  void StopAll(); //The stop all button call (MultiStop) should end up here.

  // Call this when background polling detects a spontaneous core state
  // change.
  void SpontaneousCoreStatusChange(1: CoreId core, 2: CoreStatus status);
  CoreStatus GetCoreStatus(1: CoreId core);
  
  bool IsItOkToStopCore(1: CoreId core);
  
  void SetCPUStatusPolling(1: bool on);
  
  void AckCPUStatusPolling(1: i32 coreCount);


}

//=============================================================================
//ExecutionControl is for starting and stoping cores on any partner. Just like
// a distributed DkGo().
//It is currently implemented on top of the event bus, instead of as a proper 
//service. The reason is that we need the broadcast functionality.


//some event names.
const string EXEC_CONTROL_GO_EVENT        = "go.exec_control"
const string EXEC_CONTROL_STOP_EVENT      = "stop.exec_control"
const string EXEC_CONTROL_MULTISTOP_EVENT = "multistop.exec_control"
const string EXEC_CONTROL_MULTIGO_EVENT   = "multigo.exec_control"
const string EXEC_CONTROL_SET_RUNALL_EVENT = "runall.exec_control"
const string EXEC_CONTROL_SET_RUNONE_EVENT = "runone.exec_control"

//go and stop events may have multiple cores, each gets its own key with this as a prefix. e.g. core0=>"17" core1=>"3"
const string EXEC_CONTROL_EVENT_CORE_KEY_PREFIX = "core"

service ExecutionControl {
	oneway void Go(1: CoreId core);
	oneway void Stop(1: CoreId core);
	oneway void MultiStop();
	oneway void MultiGo();
	oneway void SetRunAll(1: bool all);
}

//=============================================================================
//Multicorelogging.
service MultiCoreLog {
  void Log(1: CoreRange cores, 2: i32 color, 3: string timeStamp, 4: string text);
}

//=============================================================================
//SoftCTI

//some event names.
const string SOFT_CTI_STOP_ALL_EVENT        = "stopall.soft_cti"
const string SOFT_CTI_EVENT_CORE_KEY_PREFIX = "core"
const string SOFT_CTI_EVENT_ID_KEY_PREFIX = "id"

service SoftCTI {
  	void SetSoftCTI(1: bool on);
  	bool GetSoftCTI();
	oneway void CoreStarting(1: CoreId core);
	i64 CoreStopping(1: CoreId core);  	  //Return "CTI-id"
}

//=============================================================================
//CPUStatusPollControl

//some event names.
const string CPU_STATUS_POLL_CONTROL_DISABLE_EVENT        = "disable.cpu_status_poll_control"
const string CPU_STATUS_POLL_CONTROL_ENABLE_EVENT        = "enable.cpu_status_poll_control"
//const string SOFT_CTI_EVENT_CORE_KEY_PREFIX = "core"
//const string SOFT_CTI_EVENT_ID_KEY_PREFIX = "id"

service CPUStatusPollControl {
  	void SetCPUStatusPoll(1: CoreId core, 2: bool on);
  	bool GetCPUStatusPoll();
}


//=============================================================================
// DebuggerStarter


const string DEBUGGER_STARTER_TOOL_TO_START_KEY = "toolToStart"

enum Tools {
	EmbeddedWorkbench,
	CSpyBat,
	CSpyServer,
	CSpyRuby
}

exception FailedToStartPartner {
	1: required PartnerId partner
	2: required string message
}

service DebuggerStarter extends shared.HeartbeatService {
	void Configure(1: string stageDir, 2: Tools defaultTool);
	
	ServiceRegistry.ServiceLocation StartServiceRegistry();
	
	//!Spawns debugger processes for each partner. consults the partner registry 
	// to see if the partner is alive.
	// The partner registry is expected to be found in the sharedServiceRegistry. 
	// On return the partner is not guaranteed to be alive, but the process has been started (if they werent alive according to the partner registry).
	
	//All partners that are started each get passed:
    //	* a ready made private service (sub)registry of parentServiceRegistryLoc,
	//  * the sharedServiceRegistry that is shared among the partners (currently for ampSync services)
	// 
	// for each partner p to start p.configuration["toolToStart"] must be one of the things in enum Tools.
	
	// This method returns a list of sub-registry locations within parentServiceRegistryLoc for each partner, in partner order
	list<ServiceRegistry.ServiceLocation> StartDebuggers(1: ServiceRegistry.ServiceLocation parentServiceRegistryLoc,
						2: ServiceRegistry.ServiceLocation sharedServiceRegistryLoc) throws(1: FailedToStartPartner fsp, 2: ServiceRegistry.ServiceException se)

	//!Terminates all debuggers spawned so far by this service, in reverse startup order.
	// The service will perform a best-effort attempt to shutdown each debugger cleanly, but it might forcibly terminate the debugger process if necessary.
	void TerminateDebuggers() throws(1: ServiceRegistry.ServiceException se)

	//!Get the registry namespace for a specific partner
	string GetPartnerNamespace(1: PartnerId partnerId, 2: ServiceRegistry.ServiceLocation sharedServiceRegistryLoc) throws (1: UnknownPartner partner)
}


const string DEBUGKERNEL_EXEC_STARTED_EVENT = "debugkernel.execstarted"
const string DEBUGKERNEL_EXEC_STOPPED_EVENT = "debugkernel.execstopped"
const string DEBUGKERNEL_RESET_EVENT = "debugkernel.reset"
const string DEBUGKERNEL_BEGIN_SESSION_EVENT = "debugkernel.beginsession"
const string DEBUGKERNEL_END_SESSION_EVENT = "debugkernel.endsession"
const string DEBUGKERNEL_MEMMOY_CHANGED_EVENT = "debugkernel.memorychanged"
const string DEBUGKERNEL_SENDER = "sender"
