namespace cpp CSpy.Sim.Thrift
namespace java com.iar.cspy.simulator

include "shared.thrift"

const string SIM_SERVICE = "simulator";

service Simulator
{
  i64 getFrequency();
  void setFrequency(1: i64 freq);
}
