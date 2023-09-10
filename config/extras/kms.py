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
        self.config = config
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.reactor = self.printer.get_reactor()

        # Register KMS macros
        self.gcode.register_command('KMS_HELP', self.cmd_KMS_HELP, desc = self.cmd_KMS_HELP_help)
        self.gcode.register_command('KMS_LOAD_TOOL', self.cmd_KMS_LOAD_TOOL, desc = self.cmd_KMS_LOAD_TOOL_help)

        self._setup_kms_hardware(config)

    def _setup_kms_hardware(self, config):
        self.splitter_sensor = self.printer.lookup_object("filament_switch_sensor kms_switch_sensor_splitter_1", None)

        if self.splitter_sensor is None:
        	raise self.config.error("Missing [filament_switch_sensor kms_switch_sensor_splitter_1] section in kms_hardware.cfg")

    cmd_KMS_HELP_help = "Display the complete set of MMU commands and function"
    def cmd_KMS_HELP(self, gcmd):
        self.gcode.respond_info("KMS_HELP test")

    cmd_KMS_LOAD_TOOL_help = "Move the filament to the Y splitter and retract"
    def cmd_KMS_LOAD_TOOL(self, gcmd):
        tool_name = gcmd.get('TOOL', "!")
        filament_loaded = 0
        self.gcode.respond_info("KMS_LOAD_TOOL tool_name: %s, filament_present: %s" % (tool_name, self.splitter_sensor.runout_helper.filament_present))


        cmd_move_stepper = ("MANUAL_STEPPER STEPPER=\"kms_mmu_1_%s\" SET_POSITION=0" % (tool_name))
        self.gcode.run_script_from_command(cmd_move_stepper)
        for index in range(1, 50):
        	cmd_move_stepper = ("MANUAL_STEPPER STEPPER=\"kms_mmu_1_%s\" MOVE=%f SPEED=15 ACCEL=0 SYNC=0" % (tool_name, index * 5))
        	self.gcode.respond_info("Filament status: %s" % (self.splitter_sensor.runout_helper.filament_present))
        	self.gcode.run_script_from_command(cmd_move_stepper)
        	if self.splitter_sensor.runout_helper.filament_present == "True":
        		self.gcode.respond_info("Filament detected on splitter")
        		filament_loaded = 1
        		cmd_move_stepper = ("MANUAL_STEPPER STEPPER=\"kms_mmu_1_%s\" SET_POSITION=0" % (tool_name))
        		self.gcode.run_script_from_command(cmd_move_stepper)
        		cmd_move_stepper = ("MANUAL_STEPPER STEPPER=\"kms_mmu_1_%s\"  MOVE=-30 SPEED=15 ACCEL=0 SYNC=0" % (tool_name))
        		self.gcode.run_script_from_command(cmd_move_stepper)
        		break

        if filament_loaded:
        	command_string = ("%s TOOL=%s" % (self.MACRO_KMS_TOOL_STATUS_READY, tool_name))
        	self.gcode.run_script_from_command(command_string)
        else:
        	command_string = ("%s TOOL=%s" % (self.MACRO_KMS_TOOL_STATUS_ERROR, tool_name))
        	self.gcode.run_script_from_command(command_string)



def load_config(config):
    return Kms(config)    	
