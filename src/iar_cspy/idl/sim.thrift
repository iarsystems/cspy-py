namespace cpp CSpy.Sim.Thrift
namespace java com.iar.cspy.simulator

include "shared.thrift"

const string SIM_SERVICE = "simulator";
const shared.ServiceVersion SIM_VERSION = {
  "major": 1, // Increment for breaking changes
  "minor": 0  // Increment for non-breaking changes
};

service Simulator
{
  /** Get the service version hosted by the server. */
  shared.ServiceVersion GetServiceVersion();

  i64 getFrequency();
  void setFrequency(1: i64 freq);
}
