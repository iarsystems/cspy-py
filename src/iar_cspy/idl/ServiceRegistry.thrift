namespace java com.iar.services.registry
namespace cpp ServiceRegistry.Thrift

// Name of environment variable used to propagate location of
// service registry.
const string IAR_SERVICE_REGISTRY_ENVVAR = "IAR_SERVICE_REGISTRY"

// Prefix used for named pipes
const string IAR_CSPY_PIPE_PREFIX = "iar-cspy"

// Service name for the registry itself
const string SERVICE_REGISTRY_SERVICE = "com.iar.thrift.service.registry";

enum Protocol {
  Binary,
  Json
}

enum Transport {
  /*
   * Regular TCP socket.
   */
  Socket,
  
  /*
   * On Windows this maps to "named pipes".
   * On Unix, this maps to AF_UNIX sockets. 
   */
  Pipe,
}

struct ServiceLocation {
  /*
   * If transport == Socket, 'host' is the hostname of the server.
   * If transport == Pipe, 'host' is the full name of the native pipe.
   * On Unix this is the filename of the AF_UNIX type socket. On Windows
   * this is the path of the named pipe, on the form \\\\.\\pipe\\PIPENAME.
   */
  1: string host,
  
  // 'port' is unused if transport == Pipe and should be set to 0.
  2: i32 port,
  
  3: Protocol protocol,
  
  4: Transport transport
}

exception ServiceException {
  1: string message,          // Message
  2: string serviceName,      // The name of the originating service
  3: string exceptionDescr    // Description of exception (if any)
}

// Listener interface for the service registry.
service ServiceListener {
  
  // Invoked by the service registry when the set of services changed,
  // according to the filter specified in addServiceListener().
  oneway void servicesChanged(1: map<string, ServiceLocation> services)
}

service CSpyServiceRegistry {
  
  // Wait until a service is available.
  ServiceLocation waitForService(1: string serviceName, 2: i32 timeout) 
    throws (1:ServiceException e),
  
  void registerService(1: string serviceName, 2: ServiceLocation location)
    throws (1:ServiceException e),
  
  void deregisterService(1: string serviceName),
  
  // Adds the given service as a listener for services being added/removed.
  // Only services whose service ids matches the given regex are reported.
  // Service listeners must be registered first (so that they have an id).
  // The service registry will start by sending the listener the current set
  // of matching services.
  void addServiceListener(1: string filterRegex, 2: string serviceListenerId)
    throws (1: ServiceException e),
  
  // Removes the given service as a listener. Services are also removed 
  // as listeners when they are deregistered.
  void removeServiceListener(2: string serviceListenerId),

  // Returns the current map of services.
  map<string, ServiceLocation> getServices()

  // Return true iff the registry is still alive and serving requests.
  void isAlive();

  // Returns a list of supported transports. Attempting to register a
  // service uses a transport which is not supported will result in an
  // exception.
  list<Transport> getSupportedTransports();

  // Create a new namespace. This will create a new server which will
  // will prefix any service with the given namespace. If the namespace already exists,
  // it is retrieved and returned. If not, it is created and returned.
  ServiceLocation createNamespace(1: string name);

  // Remove a namespace. This will terminate the corresponding server if
  // present, and remove all references to it from this registry.
  // Removing a non-existing namespace is safe and is a no-op.
  void removeNamespace(1: string name);
  
  // Get available namespaces in this registry by name.
  list<string> getNamespaces();
}
