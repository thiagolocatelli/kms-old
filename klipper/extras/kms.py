# Klipper Material Shenanigans (KMS) Software - Main module
#
# Copyright (C) 2022  .doublet (discord)

# Inspired by original ERCF, HappyHare v2 and TradRack software
# This file may be distributed under the terms of the GNU GPLv3 license.
#

#from . import manual_stepper, manual_mh_stepper, manual_extruder_stepper
from .manual_extruder_stepper import ManualExtruderStepper
from .filament_switch_sensor import SwitchSensor
from .mmu_servo import MmuServo

class KMSServo:
    def __init__(self, servo:MmuServo, toolhead):
        self.servo = servo
        self.toolhead = toolhead

    def set_servo(self, width=None, angle=None, print_time=None):
        if print_time is None:
            print_time = self.toolhead.get_last_move_time()
        if width is not None:
            self.servo._set_pwm(print_time, self.servo._get_pwm_from_pulse_width(width))
        else:
            self.servo._set_pwm(print_time, self.servo._get_pwm_from_angle(angle))


class KMSFeeder:

    SERVO_DOWN_STATE = 1
    SERVO_UP_STATE = 0
    SERVO_UNKNOWN_STATE = -1

    def __init__(self, index:int, gcode, toolhead, sensor:SwitchSensor, stepper:ManualExtruderStepper, servo:KMSServo):
        self.name = "T{}".format(index)
        self.splitter_endstop_name = "kms_splitter_endstop_{}".format(int(index/4 +1))
        self.virtual_endstop_name = "kms_virtual_endstop_T{}".format(index)
        self.gcode = gcode
        self.toolhead = toolhead
        self.sensor = sensor
        self.stepper = stepper
        self.servo = servo
        self.servo_state = self.servo_angle = self.SERVO_UNKNOWN_STATE

    def _servo_up(self, servo_angle_up, wait_moves=True, print_time=None):
        if wait_moves:
            self.toolhead.dwell(0.2)
            self.toolhead.wait_moves()
        self.servo.set_servo(angle=servo_angle_up, print_time=print_time)
        self.servo_angle = servo_angle_up
        self.servo_state = self.SERVO_UP_STATE

    def _servo_down(self, servo_angle_down, servo_wait, toolhead_dwell=False, buzz_gear=True):
        self.toolhead.wait_moves()
        self.servo.set_servo(angle=servo_angle_down)
        if self.servo_angle != servo_angle_down and buzz_gear:
            oscillations = 4
            for i in range(oscillations):
                self.toolhead.dwell(0.05)
                self.stepper.do_move(0.5, 25, 1000, False)
                self.toolhead.dwell(0.05)
                self.stepper.do_move(-0.5, 25, 1000, (i == oscillations - 1))       
        if toolhead_dwell:
            self.toolhead.dwell(servo_wait)
        self.servo_angle = servo_angle_down
        self.servo_state = self.SERVO_DOWN_STATE

    def _home_filament_splitter(self, load_initial_length, load_moves_speed, load_moves_accel, load_retraction_length):
        self.stepper.do_set_position(0.)
        self.stepper.do_mh_homing_move(load_initial_length, load_moves_speed, load_moves_accel, 
                                            triggered=True, check_trigger=True, 
                                            endstop_name=self.splitter_endstop_name)
        self.toolhead.wait_moves()
        self.stepper.do_set_position(0.)
        self.stepper.do_move(-1 * load_retraction_length, load_moves_speed, 
                                    load_moves_accel, False)

    def _home_filament_toolhead(self, load_initial_length, load_moves_speed, load_moves_accel):
        self.stepper.do_set_position(0.)
        self.stepper.do_mh_homing_move(load_initial_length, load_moves_speed, load_moves_accel, 
                                            triggered=True, check_trigger=True, 
                                            endstop_name=self.virtual_endstop_name)
        self.toolhead.wait_moves()

class Kms:

    # Neopixel Macros
    MACRO_KMS_TOOL_STATUS_READY = "_KMS_TOOL_STATUS_READY"
    MACRO_KMS_TOOL_STATUS_LOADING = "_KMS_TOOL_STATUS_LOADING"
    MACRO_KMS_TOOL_STATUS_RETRACTING = "_KMS_TOOL_STATUS_RETRACTING"
    MACRO_KMS_TOOL_STATUS_FREE = "_KMS_TOOL_STATUS_FREE"
    MACRO_KMS_TOOL_STATUS_ERROR = "_KMS_TOOL_STATUS_ERROR"

    # KMS Variables
    VAR_LOADED_TOOL = "kms_loaded_tool"

    def __init__(self, config):
        self.name = config.get_name().split()[-1]
        self.config = config
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.reactor = self.printer.get_reactor()

        # Initialization
        self.feeders: list[KMSFeeder] = []
        self.split_sensors = []

        # KMS parameters
        kms_config = config.getsection('kms')
        self.number_of_units = kms_config.getint('number_of_units', 1, minval=1)
        self.number_of_tools = kms_config.getint('number_of_tools', 4, minval=1)
        self.load_initial_length = kms_config.getfloat('load_initial_length', 500., above=0.)
        self.load_incremental_length = kms_config.getfloat('load_incremental_length', 25., above=0.)
        self.load_retraction_length = kms_config.getfloat('load_retraction_length', 50., above=0.)
        self.load_moves_speed = kms_config.getfloat('load_moves_speed', 100., above=0.)
        self.load_moves_accel = kms_config.getfloat('load_moves_accel', 400., above=0.)
        self.servo_angle_up = kms_config.getint('servo_angle_up', 30, minval=1)
        self.servo_angle_down = kms_config.getint('servo_angle_down', 95, minval=1)
        self.servo_wait = config.getfloat('servo_wait_ms', default=500., above=0.) / 1000.

        # Register Klipper event handlers
        self.printer.register_event_handler("klippy:connect", self.handle_connect)
        self.printer.register_event_handler("klippy:ready", self._update_status_startup)

        # Register KMS macros
        self.gcode.register_command('KMS_HELP', self.cmd_KMS_HELP, desc=self.cmd_KMS_HELP_help)
        self.gcode.register_command('_KMS_LOAD_TOOL', self.cmd_KMS_LOAD_TOOL, desc=self.cmd_KMS_LOAD_TOOL_help)
        for tool_num in range(self.number_of_tools):
            self.gcode.register_command('T{}'.format(tool_num), self.cmd_SELECT_TOOL, desc=self.cmd_SELECT_TOOL_help)


    def handle_connect(self):
        self.toolhead = self.printer.lookup_object('toolhead') 
        self._setup_kms_hardware()     


    cmd_KMS_HELP_help = "Display the complete set of MMU commands and function"
    def cmd_KMS_HELP(self, gcmd):
        self.gcode.respond_info("KMS_HELP test")


    def _setup_kms_hardware(self):
        # Load all feeder sensors, steppers and servos
        for idx_tool in range(self.number_of_tools): 
            feed_sensor = self.printer.lookup_object("filament_switch_sensor kms_switch_sensor_T%d" % (idx_tool), None)
            if feed_sensor is None:
                raise self.config.error("Missing [filament_switch_sensor kms_switch_sensor_T%d] section in kms_hardware.cfg" % (idx_tool))  

            feed_stepper = self.printer.lookup_object("manual_extruder_stepper kms_mmu_feeder_T%d" % (idx_tool), None)
            if feed_stepper is None:
                raise self.config.error("Missing [manual_extruder_stepper kms_mmu_feeder_T%d] section in kms_hardware.cfg" % (idx_tool))  

            feed_servo = self.printer.lookup_object("mmu_servo kms_feeder_servo_T%d" % (idx_tool), None)
            if feed_servo is None:
                raise self.config.error("Missing [mmu_servo kms_feeder_servo_T%d] section in kms_hardware.cfg" % (idx_tool))  
            self.feeders.append(KMSFeeder(idx_tool, self.gcode, self.toolhead, feed_sensor, feed_stepper, KMSServo(feed_servo, self.toolhead)))

        # Load all splitter sensors
        for idx_y_sensor in range(self.number_of_units):
            splitter_sensor = self.printer.lookup_object("filament_switch_sensor kms_switch_sensor_splitter_%d" % (idx_y_sensor + 1), None)
            if splitter_sensor is None:
                raise self.config.error("Missing [filament_switch_sensor kms_switch_sensor_splitter_%d] section in kms_hardware.cfg" % (idx_y_sensor + 1))  
            self.split_sensors.append(splitter_sensor)

        # Sanity check to see that kms_vars.cfg is included. This will verify path because default has single entry
        self.variables = self.printer.lookup_object('save_variables').allVariables
        if self.variables == {}:
            raise self.config.error("Calibration settings not found: mmu_vars.cfg probably not found. Check [save_variables] section in kms_software.cfg")    


    cmd_SELECT_TOOL_help = "Load filament from KMS into the toolhead with T<index> commands"
    def cmd_SELECT_TOOL(self, gcmd):
        tool = int(gcmd.get_command().partition('T')[2])
        feeder = self.feeders[tool]
        loaded_tool = self._get_variable(self.VAR_LOADED_TOOL)
        if loaded_tool == feeder.name:
            self.gcode.respond_info("cmd_SELECT_TOOL -> tool %s already loaded" % (feeder.name))
            return

        # self._save_variable('test', 'xxx', True)
        self._save_variable(self.VAR_LOADED_TOOL, feeder.name, True)
        self.gcode.respond_info("cmd_SELECT_TOOL -> tool %s \nendstop: %s\nvendstop: %s\nloaded tool: %s" % (feeder.name, 
                                                                                            feeder.splitter_endstop_name,
                                                                                            feeder.virtual_endstop_name,
                                                                                            loaded_tool))
    

    cmd_KMS_LOAD_TOOL_help = "Move the filament to the Y splitter and retract"
    def cmd_KMS_LOAD_TOOL(self, gcmd):
        tool_name = gcmd.get_int('TOOL', None, minval=0)
        if tool_name is None:
            raise self.gcode.error("_KMS_LOAD_TOOL macro requires TOOL parameter")
        filament_loaded = False

        if not self._is_in_print():
            self.gcode.run_script_from_command("%s TOOL=T%s" % (self.MACRO_KMS_TOOL_STATUS_LOADING, tool_name))
            self.toolhead.wait_moves()
            self.gcode.respond_info("KMS_LOAD_TOOL tool_name: T%s, filament_present: %s" % (tool_name, self._check_splitter_sensor(tool_name)))
            feeder = self.feeders[tool_name]
            try:
                feeder._servo_down(self.servo_angle_down, self.servo_wait)
                # feeder.stepper.do_set_position(0.)
                # feeder.stepper.do_mh_homing_move(self.load_initial_length, self.load_moves_speed, self.load_moves_accel, 
                #                                  True, True, endstop_name=feeder.splitter_endstop_name)
                # self.toolhead.wait_moves()
                # feeder.stepper.do_set_position(0.)
                # feeder.stepper.do_move(-1 * self.load_retraction_length, self.load_moves_speed, self.load_moves_accel, False)
                feeder._home_filament_splitter(self.load_initial_length, self.load_moves_speed, 
                                               self.load_moves_accel, self.load_retraction_length)
                filament_loaded = True
            except Exception as e:
                self.gcode.respond_info("Did not home: %s" % str(e))
            finally: 
                feeder._servo_up(self.servo_angle_up)  

            if filament_loaded:
                self.gcode.run_script_from_command("%s TOOL=T%s" % (self.MACRO_KMS_TOOL_STATUS_READY, tool_name))
            else:
                self.gcode.run_script_from_command("%s TOOL=T%s" % (self.MACRO_KMS_TOOL_STATUS_ERROR, tool_name))
   
        else:
            self.gcode.respond_info("KMS tool T%s: Unable to load new filament during print" % (tool_name))


    def _update_status_startup(self):
        self.gcode.run_script_from_command("FLICKER")
        self.gcode.respond_info("self.feeders length: %d" % len(self.feeders))
        self.gcode.respond_info("print status: %s" % self._get_print_status())
        for feeder_idx, feeder in enumerate(self.feeders):
            if feeder.sensor.runout_helper.filament_present:
                self.gcode.run_script_from_command("%s TOOL=T%s" % (self.MACRO_KMS_TOOL_STATUS_READY, feeder_idx))
            else:
                self.gcode.run_script_from_command("%s TOOL=T%s" % (self.MACRO_KMS_TOOL_STATUS_FREE, feeder_idx))


    def _save_variable(self, name:str, value, is_str=True):
        if is_str:
            self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE='\"%s\"'" %  (name, str(value)))
        else:
            self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE=%d" %  (name, value))


    def _get_variable(self, name:str):
        variables = self.printer.lookup_object('save_variables').allVariables
        return variables.get(name, None)
    

    def _check_splitter_sensor(self, tool_name):
        if self.split_sensors[int(int(tool_name) / 4)].runout_helper.filament_present:
            return 1
        else:
            return 0


    def _is_in_print(self):
        return self._get_print_status() == "printing"


    def _is_in_pause(self):
        return self._get_print_status() == "paused"


    def _is_in_standby(self):
        return self._get_print_status() == "standby"


    def _get_print_status(self):
        try:
            # If using virtual sdcard this is the most reliable method
            source = "print_stats"
            print_status = self.printer.lookup_object("print_stats").get_status(self.printer.get_reactor().monotonic())['state']
        except:
            # Otherwise we fallback to idle_timeout
            source = "idle_timeout"
            if self.printer.lookup_object("pause_resume").is_paused:
                print_status = "paused"
            else:
                idle_timeout = self.printer.lookup_object("idle_timeout").get_status(self.printer.get_reactor().monotonic())
                print_status = idle_timeout['state'].lower()
        finally:
            #self._log_trace("Determined print status as: %s from %s" % (print_status, source))
            return print_status

def load_config(config):
    return Kms(config)    	
