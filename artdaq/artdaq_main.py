import artdaq
from artdaq.constants import AcquisitionType, TerminalConfiguration, Slope, Edge, WAIT_INFINITELY

task = artdaq.Task()


def create_AI_voltage_channel(physicalChannel, terminal_config=10078, min_val=-10, max_val=10):
    terminal_config_dict = {
        -1: TerminalConfiguration.DEFAULT,
        10083: TerminalConfiguration.RSE,
        10078: TerminalConfiguration.NRSE,
        10106: TerminalConfiguration.DIFFERENTIAL,
        12529: TerminalConfiguration.PSEUDODIFFERENTIAL
    }
    task.ai_channels.add_ai_voltage_chan(physicalChannel, terminal_config=terminal_config_dict[terminal_config],
                                         min_val=min_val, max_val=max_val)


def config_sample_clk_timing(rate=10000, sample_mode=10178, samps_per_chan=15000):
    sample_mode_dict = {
        10178: AcquisitionType.FINITE,
        10123: AcquisitionType.CONTINUOUS
    }
    task.timing.cfg_samp_clk_timing(rate, sample_mode=sample_mode_dict[sample_mode], samps_per_chan=samps_per_chan)


def config_dig_edge_start_trig(trigger_source, trigger_edge):
    trigger_edge_dict = {
        10280: Edge.RISING,
        10171: Edge.FALLING
    }
    task.triggers.start_trigger.cfg_dig_edge_start_trig(trigger_source=trigger_source,
                                                        trigger_edge=trigger_edge_dict[trigger_edge])


def config_anlg_edge_start_trig(trigger_source, trigger_slope, trigger_level):
    trigger_slope_dict = {
        10280: Slope.RISING,
        10171: Slope.FALLING
    }
    task.triggers.start_trigger.cfg_anlg_edge_start_trig(trigger_source=trigger_source,
                                                         trigger_slope=trigger_slope_dict[trigger_slope],
                                                         trigger_level=trigger_level)


def start_task():
    task.start()


def read_anlg_f64(num_samps_per_chan=15000, timeout=WAIT_INFINITELY):
    data = task.read(number_of_samples_per_channel=num_samps_per_chan, timeout=timeout)
    return data


def stop_task():
    task.stop()


def close_task():
    task.close()


# def main():
#     with artdaq.Task() as task:
#         task.ai_channels.add_ai_voltage_chan("Dev42/ai0:6", terminal_config=terminal_config,
#                                              min_val=min_val, max_val=max_val,
#                                              )
#         task.timing.cfg_samp_clk_timing(rate, sample_mode=sample_mode, samps_per_chan=samps_per_chan)
#         task.triggers.start_trigger.cfg_anlg_edge_start_trig(trigger_source=trigger_source,
#                                                              trigger_slope=trigger_slope, trigger_level=trigger_level)
#         task.start()
#         data = task.read(number_of_samples_per_channel=num_samps_per_chan, timeout=timeout)
#         task.stop()


# create_AI_voltage_channel("Dev42/ai0:15", terminal_config=10078, min_val=-10, max_val=10)
# config_anlg_edge_start_trig('Dev42/ai1', 10280, 1)
# config_sample_clk_timing(1000, 10178, 1000)
# start_task()
# data = read_anlg_f64(1000, 2)
# # print(data)
# # print(len(data[0]))
# print(data == [[0 for _ in range(1000)] for _ in range(16)])
# stop_task()
# close_task()
