class Kms:

    # Neopixel Macros
    MACRO_SERVO_UP = "_KMS_SERVO_UP"
    MACRO_SERVO_DOWN = "_KMS_SERVO_DOWN"

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

        # KMS parameters
        self.load_initial_length = config.getfloat('load_initial_length', 500. ,above=0.)
        self.load_incremental_length = config.getfloat('load_incremental_length', 25. ,above=0.)
        self.load_retraction_length = config.getfloat('load_retraction_length', 50. ,above=0.)
        self.load_moves_speed = config.getfloat('load_moves_speed', 100. ,above=0.)
        self.load_moves_accel = config.getfloat('load_moves_accel', 400. ,above=0.)

        self.printer.register_event_handler("klippy:connect", self.handle_connect)

        # Register KMS macros
        self.gcode.register_command('KMS_HELP', self.cmd_KMS_HELP, desc = self.cmd_KMS_HELP_help)
        self.gcode.register_command('_KMS_LOAD_TOOL', self.cmd_KMS_LOAD_TOOL, desc = self.cmd_KMS_LOAD_TOOL_help)
        self.gcode.register_command('_KMS_CHANGE_TOOL', self.cmd_KMS_CHANGE_TOOL, desc = self.cmd_KMS_CHANGE_TOOL_help)

        self._setup_kms_hardware(config)

    def handle_connect(self):
        self.toolhead = self.printer.lookup_object('toolhead')        

    def _setup_kms_hardware(self, config):
        self.splitter_sensor = self.printer.lookup_object("filament_switch_sensor kms_switch_sensor_splitter_1", None)

        if self.splitter_sensor is None:
        	raise self.config.error("Missing [filament_switch_sensor kms_switch_sensor_splitter_1] section in kms_hardware.cfg")
        
    cmd_KMS_HELP_help = "Display the complete set of MMU commands and function"
    def cmd_KMS_HELP(self, gcmd):
        self.gcode.respond_info("KMS_HELP test")

    cmd_KMS_CHANGE_TOOL_help = "Move the filament to the Y splitter and retract"
    def cmd_KMS_CHANGE_TOOL(self, gcmd):   
        tool_name = gcmd.get('TOOL', "!")
        self.gcode.respond_info("KMS_CHANGE_TOOL tool = %s" % (tool_name)) 


    cmd_KMS_LOAD_TOOL_help = "Move the filament to the Y splitter and retract"
    def cmd_KMS_LOAD_TOOL(self, gcmd):
        tool_name = gcmd.get('TOOL', "!")
        filament_loaded = 0

        self.gcode.respond_info("KMS_LOAD_TOOL tool_name: %s, filament_present: %s" % (tool_name, self._check_splitter_sensor()))
        self.gcode.run_script_from_command("%s TOOL=%s" % (self.MACRO_SERVO_DOWN, tool_name))

        feeder_stepper = self.printer.lookup_object(("manual_extruder_stepper kms_mmu_1_%s" % (tool_name)), None)
        feeder_stepper.do_set_position(0.)
        feeder_stepper.do_move(self.load_initial_length, self.load_moves_speed, self.load_moves_accel, False)
        self.toolhead.dwell(0.2)
        for index in range(1, 40):
            if self._check_splitter_sensor() == 1:
                filament_loaded = 1
                feeder_stepper.do_set_position(0.)
                feeder_stepper.do_move(-1 * self.load_retraction_length, self.load_moves_speed, self.load_moves_accel, False)
                self.toolhead.wait_moves()
                break
            else:
                feeder_stepper.do_move(float(self.load_initial_length + (index * self.load_incremental_length)), 
                                       self.load_moves_speed, self.load_moves_accel, False)
                self.toolhead.wait_moves()

        self.gcode.run_script_from_command("%s TOOL=%s" % (self.MACRO_SERVO_UP, tool_name))
        if filament_loaded:
            self.gcode.run_script_from_command("%s TOOL=%s" % (self.MACRO_KMS_TOOL_STATUS_READY, tool_name))
        else:
            self.gcode.run_script_from_command("%s TOOL=%s" % (self.MACRO_KMS_TOOL_STATUS_ERROR, tool_name))

    def _check_splitter_sensor(self):
        if self.splitter_sensor.runout_helper.filament_present:
            return 1
        else:
            return 0

def load_config(config):
    return Kms(config)    	
