namespace java com.iar.services.registry
namespace cpp ServiceManager.Thrift

include "ServiceRegistry.thrift"

const string SERVICE_MANAGER_SERVICE = "com.iar.thrift.service.manager";

struct ServiceConfig
{
  1: string name
  2: string libraryName
  3: bool spawnNewProcess
  4: string startupEntryPoint
  5: string shutdownEntryPoint
  6: bool registerInLauncher
  // 7: list<string> dependencies // TODO: Dependencies are not supported yet
}

/**
 A Thrift service to manage other Thrift services.
 A manager knows about a set of Thrift services, which are presented to it
 via ServiceConfig instances and/or externally (e.g. service manifest files).
 */
service CSpyServiceManager {

  /**
    Start the described service.
    Implementations might be asynchronous, so returning from this method will
    not guarantee that the service is present.
    An error during the service initialization will result in an exception 
    being thrown.
    */
  void startService(1: ServiceConfig serviceConfig)
    throws (1: ServiceRegistry.ServiceException e);

  /**
    Stop the described service.
    Implementations might be asynchronous, so returning from this method will
    not guarantee that the service has actually been destroyed.
    An error during the service shutdown will result in an exception 
    being thrown.
    */
  void stopService(1: ServiceConfig serviceConfig)
    throws (1: ServiceRegistry.ServiceException e);

  /**
    Start all the services described by the JSON manifest file at the provided path,
    in the order they are specified.
    <p>
    Each JSON manifest file contains a list of {@link #ServiceConfig} (<tt>services</tt>):
    <pre>
    {
	   "services":[
	       {
	       "name":"com.iar.OptionHandler.service",
	       "libraryName":"OptionHandler", 
	       "spawnNewProcess":false,
	       "startupEntryPoint":"StartOptionHandlerService",
	       "shutdownEntryPoint":"StopOptionHandlerService",
	       "registerInLauncher":false
	       }
	   ]
	}
	</pre>
	
	where the library specified in libraryName is expected in the same folder as the JSON file itself.
	
    <p>
    Implementations might be asynchronous, so returning from this method will
    not guarantee that the service is present.
    An error during the service initialization will result in an exception 
    being thrown.
    */
  void startServicesFromJsonManifest(1: string jsonFilePath)
    throws (1: ServiceRegistry.ServiceException e);

  /**
    Stop the service described by the JSON manifest file at the provided path, 
    in reverse order with respect to their specification in the manifest.
    <p>
    See {@link #startServicesFromJsonManifest} for further details on the JSON manifest file format.
    <p>
    Implementations might be asynchronous, so returning from this method will
    not guarantee that the service is present.
    An error during the service shutdown will result in an exception 
    being thrown.
    */
  void stopServicesFromJsonManifest(1: string jsonFilePath)
    throws (1: ServiceRegistry.ServiceException e);

  /** 
    Shutdown the service manager, destroying all active services.
    Implementations might be asynchronous, so returning from this method will
    not guarantee that the service manager is indeed shutdown.
    */
  void shutdown()
    throws (1: ServiceRegistry.ServiceException e); 
}

struct LauncherConfig
{
  1: bool useInternalRegistry
  2: ServiceRegistry.ServiceLocation externalRegistryLocation
  3: list<ServiceRegistry.Transport> preferredTransports  
}
