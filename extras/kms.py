

class KMSServo:
    def __init__(self, servo, toolhead):
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
    def __init__(self, name: str, toolhead, sensor, stepper, servo:KMSServo):
        self.name = name
        self.toolhead = toolhead
        self.sensor = sensor
        self.stepper = stepper
        self.servo = servo

    def _servo_up(self, servo_angle_up, wait_moves=True, print_time=None):
        if wait_moves:
            self.toolhead.wait_moves()
        self.servo.set_servo(angle=servo_angle_up, print_time=print_time)

    def _servo_down(self, servo_angle_down, servo_wait, toolhead_dwell=False):
        self.toolhead.wait_moves()
        self.servo.set_servo(angle=servo_angle_down)
        if toolhead_dwell:
            self.toolhead.dwell(servo_wait)

class Kms:

    # Neopixel Macros
    #MACRO_SERVO_UP = "_KMS_SERVO_UP"
    #MACRO_SERVO_DOWN = "_KMS_SERVO_DOWN"

    # Neopixel Macros
    MACRO_KMS_TOOL_STATUS_READY = "_KMS_TOOL_STATUS_READY"
    MACRO_KMS_TOOL_STATUS_LOADING = "_KMS_TOOL_STATUS_LOADING"
    MACRO_KMS_TOOL_STATUS_RETRACTING = "_KMS_TOOL_STATUS_RETRACTING"
    MACRO_KMS_TOOL_STATUS_FREE = "_KMS_TOOL_STATUS_FREE"
    MACRO_KMS_TOOL_STATUS_ERROR = "_KMS_TOOL_STATUS_ERROR"

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
        #self.gcode.register_command('_KMS_CHANGE_TOOL', self.cmd_KMS_CHANGE_TOOL, desc=self.cmd_KMS_CHANGE_TOOL_help)
        for tool_num in range(self.number_of_tools):
            self.gcode.register_command('_T{}'.format(tool_num), self.cmd_SELECT_TOOL, desc=self.cmd_SELECT_TOOL_help)


    def handle_connect(self):
        self.toolhead = self.printer.lookup_object('toolhead') 
        self._setup_kms_hardware()     


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
            self.feeders.append(KMSFeeder('T{}'.format(idx_tool), self.toolhead, feed_sensor, feed_stepper, KMSServo(feed_servo, self.toolhead)))

        # Load all splitter sensors
        for idx_y_sensor in range(self.number_of_units):
            splitter_sensor = self.printer.lookup_object("filament_switch_sensor kms_switch_sensor_splitter_%d" % (idx_y_sensor + 1), None)
            if splitter_sensor is None:
                raise self.config.error("Missing [filament_switch_sensor kms_switch_sensor_splitter_%d] section in kms_hardware.cfg" % (idx_y_sensor + 1))  
            self.split_sensors.append(splitter_sensor)


    cmd_KMS_HELP_help = "Display the complete set of MMU commands and function"
    def cmd_KMS_HELP(self, gcmd):
        self.gcode.respond_info("KMS_HELP test")


    cmd_KMS_CHANGE_TOOL_help = "Move the filament to the Y splitter and retract"
    def cmd_KMS_CHANGE_TOOL(self, gcmd):   
        tool_name = gcmd.get_int('TOOL', None, minval=0)
        if tool_name is None:
            raise self.gcode.error("_KMS_CHANGE_TOOL macro requires TOOL parameter")
        self.gcode.respond_info("KMS_CHANGE_TOOL tool = %s" % (tool_name)) 


    cmd_SELECT_TOOL_help = "Load filament from KMS into the toolhead with T<index> commands"
    def cmd_SELECT_TOOL(self, gcmd):
        tool = int(gcmd.get_command().partition('T')[2])
        self.gcode.respond_info("cmd_SELECT_TOOL -> tool T%d" % tool)
    

    cmd_KMS_LOAD_TOOL_help = "Move the filament to the Y splitter and retract"
    def cmd_KMS_LOAD_TOOL(self, gcmd):
        tool_name = gcmd.get_int('TOOL', None, minval=0)
        if tool_name is None:
            raise self.gcode.error("_KMS_LOAD_TOOL macro requires TOOL parameter")
        filament_loaded = 0

        self.gcode.respond_info("KMS_LOAD_TOOL tool_name: T%s, filament_present: %s" % (tool_name, self._check_splitter_sensor(tool_name)))
        feeder = self.feeders[tool_name]
        feeder._servo_down(self.servo_angle_down, self.servo_wait)

        feeder.stepper.do_set_position(0.)
        feeder.stepper.do_move(self.load_initial_length, self.load_moves_speed, self.load_moves_accel, False)
        self.toolhead.dwell(0.2)
        for index in range(1, 40):
            if self._check_splitter_sensor(tool_name) == 1:
                filament_loaded = 1
                feeder.stepper.do_set_position(0.)
                feeder.stepper.do_move(-1 * self.load_retraction_length, self.load_moves_speed, self.load_moves_accel, False)
                self.toolhead.wait_moves()
                break
            else:
                feeder.stepper.do_move(float(self.load_initial_length + (index * self.load_incremental_length)), self.load_moves_speed, self.load_moves_accel, False)
                self.toolhead.wait_moves()

        feeder._servo_up(self.servo_angle_up)
        if filament_loaded:
            self.gcode.run_script_from_command("%s TOOL=T%s" % (self.MACRO_KMS_TOOL_STATUS_READY, tool_name))
        else:
            self.gcode.run_script_from_command("%s TOOL=T%s" % (self.MACRO_KMS_TOOL_STATUS_ERROR, tool_name))


    def _check_splitter_sensor(self, tool_name):
        if self.split_sensors[int(int(tool_name) / 4)].runout_helper.filament_present:
            return 1
        else:
            return 0


    def _update_status_startup(self):
        self.gcode.run_script_from_command("FLICKER")
        self.gcode.respond_info("self.feeders length: %d" % len(self.feeders))
        for feeder_idx, feeder in enumerate(self.feeders):
            if feeder.sensor.runout_helper.filament_present:
                self.gcode.run_script_from_command("%s TOOL=T%s" % (self.MACRO_KMS_TOOL_STATUS_READY, feeder_idx))
            else:
                self.gcode.run_script_from_command("%s TOOL=T%s" % (self.MACRO_KMS_TOOL_STATUS_FREE, feeder_idx))


def load_config(config):
    return Kms(config)    	
