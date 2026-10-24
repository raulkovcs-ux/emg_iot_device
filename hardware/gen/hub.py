"""FAMA-Band hub board: power, 8-channel ADS1298 front end, ESP32-S3, IMU, microSD.

Net-name conventions: power rails use KiCad power-symbol names (+BATT, +3V3,
+3V0, VBUS, GND); everything else is upper-case with an underscore.
"""
from circuit import FP, Circuit

N_CH = 8


def build():
    c = Circuit("hub", "FAMA-Band hub", [
        ("power", "Power: USB-C, charger, battery, 3V3 buck-boost, AFE supply + USB interlock"),
        ("afe", "Analog front end: ADS1298, 2.048 MHz clock, input filters, electrode connectors"),
        ("mcu", "MCU: ESP32-S3 module, IMU, microSD, buttons, LEDs"),
    ])

    # ================================================================== POWER
    c.use("power")
    c.note("USB-C is for charging, firmware and data offload ONLY while the band is OFF the arm.\n"
           "Q1 holds the AFE LDO in shutdown whenever VBUS is present, independent of firmware,\n"
           "so electrodes are never energised while a mains-referenced USB host is attached.")
    j = c.part("J", "Connector:USB_C_Receptacle_USB2.0_16P", "USB-C", "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12",
               mpn="TYPE-C-31-M-12", manufacturer="Korean Hroparts", desc="USB-C 2.0 receptacle, 16-pin")
    c.connect("VBUS_IN", *[j[p] for p in ("A4", "A9", "B4", "B9")])
    c.connect("GND", *[j[p] for p in ("A1", "A12", "B1", "B12", "S1")])
    c.connect("USB_D+", j["A6"], j["B6"])
    c.connect("USB_D-", j["A7"], j["B7"])
    c.nc(j, "A8", "B8")
    c.newrow()
    c.R("5.1k", "CC1", "GND"); c.connect("CC1", j["A5"])
    c.R("5.1k", "CC2", "GND"); c.connect("CC2", j["B5"])
    c.newrow()
    f = c.part("F", "Device:Polyfuse", "0.5A", FP["F0805"], desc="PTC resettable fuse, 0.5 A hold, 0805")
    c.connect("VBUS_IN", f["1"]); c.connect("VBUS", f["2"])
    c.flag("VBUS_IN")
    esd = c.part("U", "Power_Protection:USBLC6-2SC6", "USBLC6-2SC6", "Package_TO_SOT_SMD:SOT-23-6",
                 mpn="USBLC6-2SC6", manufacturer="STMicroelectronics", desc="USB ESD protection")
    c.connect("USB_D+", esd["1"], esd["6"]); c.connect("USB_D-", esd["3"], esd["4"])
    c.connect("GND", esd["2"]); c.connect("VBUS", esd["5"])
    c.flag("VBUS")

    c.newrow()
    chg = c.part("U", "Battery_Management:MCP73831-2-OT", "MCP73831-2", "Package_TO_SOT_SMD:SOT-23-5",
                 mpn="MCP73831T-2ACI/OT", manufacturer="Microchip", desc="Li-Po charger, 4.20 V")
    c.connect("VBUS", chg["4"]); c.connect("GND", chg["2"]); c.connect("+BATT", chg["3"])
    c.connect("CHG_PROG", chg["5"]); c.connect("CHG_STAT", chg["1"])
    c.R("2.2k", "CHG_PROG", "GND", desc="Ichg = 1000 V / 2.2 kOhm = 455 mA (0.75 C for 600 mAh)")
    c.C("4.7uF", "VBUS", "GND", "0603")
    c.C("4.7uF", "+BATT", "GND", "0603")
    c.R("1k", "VBUS", "CHG_LED_A")
    c.LED("CHG red", "CHG_LED_A", "CHG_STAT", mpn="150060RS75000", manufacturer="Wurth", desc="Charging indicator")

    c.newrow()
    jb = c.part("J", "Connector_Generic:Conn_01x02", "BATT 1S LiPo", "Connector_JST:JST_PH_S2B-PH-SM4-TB_1x02-1MP_P2.00mm_Horizontal",
                mpn="S2B-PH-SM4-TB(LF)(SN)", manufacturer="JST", desc="Battery connector (check cell polarity!)")
    c.connect("+BATT", jb["1"]); c.connect("GND", jb["2"])
    c.flag("+BATT")
    sw = c.part("SW", "Switch:SW_SPDT", "POWER", "Button_Switch_SMD:SW_SPDT_PCM12",
                mpn="PCM12SMTR", manufacturer="C&K", desc="Main power switch (hard battery disconnect)")
    c.connect("+BATT", sw["2"]); c.connect("VSYS", sw["1"]); c.nc(sw, "3")
    c.C("10uF", "VSYS", "GND", "0805")
    c.flag("VSYS")

    c.newrow()
    bb = c.part("U", "Regulator_Switching:TPS63001", "TPS63001", "Package_SON:Texas_DRC0010J_ThermalVias",
                mpn="TPS63001DRCR", manufacturer="Texas Instruments", desc="Buck-boost, fixed 3.3 V, 1.2 A")
    c.connect("VSYS", bb["5"], bb["6"], bb["7"])  # VIN, EN, PS/SYNC=1 -> forced PWM (fixed frequency, no PFM bursts)
    c.connect("BB_VINA", bb["8"])
    c.R("10", "VSYS", "BB_VINA")
    c.C("100nF", "BB_VINA", "GND")
    c.flag("BB_VINA")
    c.connect("GND", bb["3"], bb["9"], bb["11"])
    c.connect("BB_L1", bb["4"]); c.connect("BB_L2", bb["2"])
    l1 = c.part("L", "Device:L", "1.5uH", "Inductor_SMD:L_Coilcraft_XxL4020",
                mpn="XFL4020-152MEC", manufacturer="Coilcraft", desc="1.5 uH, 4x4 mm, Isat 4.6 A")
    c.connect("BB_L1", l1["1"]); c.connect("BB_L2", l1["2"])
    c.connect("+3V3", bb["1"], bb["10"])  # FB tied to VOUT on the fixed-output version
    c.C("10uF", "VSYS", "GND", "0805")
    c.C("22uF", "+3V3", "GND", "0805")
    c.C("22uF", "+3V3", "GND", "0805")
    c.C("100nF", "+3V3", "GND")
    c.flag("+3V3")

    c.newrow()
    # VBUS interlock: VBUS present -> Q1 on -> AFE_EN low -> LP5907 off -> pods + AFE unpowered
    c.R("10k", "VBUS", "VBUS_DET")
    c.R("20k", "VBUS_DET", "GND")
    q = c.part("Q", "Transistor_FET:2N7002", "2N7002", "Package_TO_SOT_SMD:SOT-23",
               mpn="2N7002", manufacturer="onsemi", desc="Hardware USB interlock for analog rail")
    c.connect("VBUS_DET", q["G"]); c.connect("GND", q["S"]); c.connect("AFE_EN", q["D"])
    c.R("10k", "AFE_EN_MCU", "AFE_EN")
    c.R("100k", "AFE_EN", "GND")

    c.newrow()
    fb = c.part("FB", "Device:FerriteBead_Small", "220R@100MHz", FP["FB0603"],
                mpn="BLM18PG221SN1D", manufacturer="Murata", desc="Ferrite bead, isolates AFE LDO input")
    c.connect("+3V3", fb["1"]); c.connect("AFE_LDO_IN", fb["2"])
    c.C("1uF", "AFE_LDO_IN", "GND")
    c.flag("AFE_LDO_IN")
    ldo = c.part("U", "Regulator_Linear:LP5907MFX-3.0", "LP5907-3.0", "Package_TO_SOT_SMD:SOT-23-5",
                 mpn="LP5907MFX-3.0/NOPB", manufacturer="Texas Instruments", desc="Ultra-low-noise LDO 3.0 V (6.5 uVrms) for AFE + electrode buffers")
    c.connect("AFE_LDO_IN", ldo["IN"]); c.connect("GND", ldo["GND"]); c.connect("AFE_EN", ldo["EN"])
    c.connect("+3V0", ldo["OUT"]); c.nc(ldo, "NC")
    c.C("1uF", "+3V0", "GND")
    c.C("100nF", "+3V0", "GND")

    c.newrow()
    fg = c.part("U", "Battery_Management:LC709203FQH-01TWG", "LC709203F", "Package_DFN_QFN:WDFN-8-1EP_4x3mm_P0.65mm_EP2.4x1.8mm",
                mpn="LC709203FQH-01TWG", manufacturer="onsemi", desc="Li-ion fuel gauge, I2C 0x0B")
    c.connect("GND", fg["1"], fg["2"], fg["9"])  # TEST tied to VSS
    c.connect("+BATT", fg["3"])
    c.connect("FG_ALRT", fg["4"]); c.connect("FG_TSW", fg["5"]); c.connect("FG_TSENSE", fg["6"])
    c.connect("I2C_SDA", fg["7"]); c.connect("I2C_SCL", fg["8"])
    c.C("100nF", "+BATT", "GND")
    c.R("10k", "FG_TSW", "FG_TSENSE", desc="Thermistor divider reference")
    ntc = c.part("TH", "Device:Thermistor_NTC", "10k NTC (DNP)", "Resistor_SMD:R_0402_1005Metric",
                 mpn="NCP15XH103F03RC", manufacturer="Murata", desc="Optional cell thermistor", dnp=True)
    c.connect("FG_TSENSE", ntc["1"]); c.connect("GND", ntc["2"])
    c.R("10k", "+3V3", "FG_ALRT")

    # ==================================================================== AFE
    c.use("afe")
    c.note("ADS1298 runs unipolar: AVDD = +3V0 (LP5907), DVDD = +3V3, VREF = 2.4 V internal.\n"
           "Clock: external 2.048 MHz +/-20 ppm (CLKSEL = 0) -> f_s = 1000.00 Hz exactly (DR = 101, HR mode).\n"
           "Channel n: pod buffer -> 2.2k + 1n (CM) / 10n (DM) EMI filter -> INnP/INnN, PGA gain 6,\n"
           "range +/-400 mV, 1 LSB = 47.7 nV. Right-leg drive: RLDOUT -> 100k -> bias electrode.")
    u = c.part("U", "famaband:ADS1298IPAG", "ADS1298IPAG", "Package_QFP:TQFP-64_10x10mm_P0.5mm",
               mpn="ADS1298IPAG", manufacturer="Texas Instruments", desc="8-ch 24-bit biopotential AFE")
    c.connect("+3V0", *[u[n] for n in u.pins_named("AVDD")], u["AVDD1"])
    c.connect("GND", *[u[n] for n in u.pins_named("AVSS")], u["AVSS1"], *[u[n] for n in u.pins_named("DGND")])
    c.connect("+3V3", *[u[n] for n in u.pins_named("DVDD")])
    c.connect("VREFP", u["VREFP"]); c.connect("GND", u["VREFN"])
    c.newrow()
    c.C("10uF", "VREFP", "GND", "0603"); c.C("100nF", "VREFP", "GND")
    for name, val, size in (("VCAP1", "22uF", "0805"), ("VCAP2", "1uF", "0402"), ("VCAP3", "1uF", "0402"), ("VCAP4", "1uF", "0402")):
        c.connect(name, u[name]); c.C(val, name, "GND", size)
    c.newrow()
    for _ in range(5):
        c.C("100nF", "+3V0", "GND")
    c.C("10uF", "+3V0", "GND", "0603")
    c.C("100nF", "+3V3", "GND"); c.C("100nF", "+3V3", "GND"); c.C("1uF", "+3V3", "GND")
    c.newrow()
    c.nc(u, "27", "29")
    c.connect("GND", u["RESV1"], u["DAISY_IN"], u["CLKSEL"])
    for sig, pin in (("AFE_DIN", "DIN"), ("AFE_PWDN", "~{PWDN}"), ("AFE_RESET", "~{RESET}"), ("AFE_CLK", "CLK"),
                     ("AFE_START", "START"), ("AFE_CS", "~{CS}"), ("AFE_SCLK", "SCLK"), ("AFE_DOUT", "DOUT"),
                     ("AFE_DRDY", "~{DRDY}")):
        c.connect(sig, u[pin])
    c.nc(u, "GPIO1", "GPIO2", "GPIO3", "GPIO4", "17", "18", "RLDREF", "RLDIN", "WCT")
    c.R("10k", "+3V3", "AFE_CS"); c.R("10k", "+3V3", "AFE_PWDN"); c.R("10k", "+3V3", "AFE_RESET")

    c.newrow()
    osc = c.part("X", "Oscillator:ECS-2520MV-xxx-xx", "2.048MHz 20ppm", "Oscillator:Oscillator_SMD_ECS_2520MV-xxx-xx-4Pin_2.5x2.0mm",
                 mpn="SiT8008BI-xx-33E-2.048000", manufacturer="SiTime", desc="2.048 MHz CMOS oscillator, 3.3 V, +/-20 ppm (2520 package)")
    c.connect("+3V3", osc["1"], osc["4"]); c.connect("GND", osc["2"]); c.connect("OSC_OUT", osc["3"])
    c.R("33", "OSC_OUT", "AFE_CLK", desc="Clock series termination")
    c.C("100nF", "+3V3", "GND")

    c.newrow()
    # right-leg drive (TI-recommended 1M || 1.5n feedback) and patient-current limit
    c.connect("RLD_INV", u["RLDINV"]); c.connect("RLD_OUT", u["RLDOUT"])
    c.R("1M", "RLD_INV", "RLD_OUT"); c.C("1.5nF", "RLD_INV", "RLD_OUT")
    c.R("100k", "RLD_OUT", "BIAS_ELEC", "0603", desc="Patient current limit: <= 30 uA even with RLDOUT shorted to a rail")
    jbias = c.part("J", "Connector_Generic:Conn_01x02", "BIAS", "Connector_JST:JST_GH_SM02B-GHS-TB_1x02-1MP_P1.25mm_Horizontal",
                   mpn="SM02B-GHS-TB(LF)(SN)", manufacturer="JST", desc="Bias (DRL) electrode")
    c.connect("BIAS_ELEC", jbias["1"]); c.connect("GND", jbias["2"])

    for ch in range(1, N_CH + 1):
        c.newrow()
        jp = c.part("J", "Connector_Generic:Conn_01x04", f"POD{ch}", "Connector_JST:JST_GH_SM04B-GHS-TB_1x04-1MP_P1.25mm_Horizontal",
                    mpn="SM04B-GHS-TB(LF)(SN)", manufacturer="JST", desc="Electrode pod connector")
        c.connect("+3V0", jp["1"]); c.connect(f"POD{ch}_P", jp["2"]); c.connect(f"POD{ch}_N", jp["3"]); c.connect("GND", jp["4"])
        c.R("2.2k", f"POD{ch}_P", f"CH{ch}_P"); c.R("2.2k", f"POD{ch}_N", f"CH{ch}_N")
        c.C("1nF", f"CH{ch}_P", "GND"); c.C("1nF", f"CH{ch}_N", "GND")
        c.C("10nF", f"CH{ch}_P", f"CH{ch}_N")
        c.connect(f"CH{ch}_P", u[f"IN{ch}P"]); c.connect(f"CH{ch}_N", u[f"IN{ch}N"])

    # ==================================================================== MCU
    c.use("mcu")
    c.note("ESP32-S3-WROOM-1-N16R8 (octal PSRAM: GPIO35-37 reserved). Strapping pins 0/3/45/46 left at defaults.\n"
           "AFE on SPI2 (GPIO9-14,21,47,48), microSD 4-bit SDMMC (GPIO1,2,38-42), I2C IMU + fuel gauge (GPIO4/5).")
    m = c.part("U", "RF_Module:ESP32-S3-WROOM-1", "ESP32-S3-WROOM-1-N16R8", "RF_Module:ESP32-S3-WROOM-1",
               mpn="ESP32-S3-WROOM-1-N16R8", manufacturer="Espressif", desc="Wi-Fi/BLE 5 MCU module, 16 MB flash, 8 MB PSRAM")
    c.connect("GND", m["1"], m["40"], m["41"]); c.connect("+3V3", m["2"])
    c.connect("ESP_EN", m["EN"])
    c.R("10k", "+3V3", "ESP_EN"); c.C("1uF", "ESP_EN", "GND")
    gpio = {"IO12": "AFE_SCLK", "IO11": "AFE_DIN", "IO13": "AFE_DOUT", "IO10": "AFE_CS", "IO9": "AFE_DRDY",
            "IO14": "AFE_START", "IO21": "AFE_RESET", "IO47": "AFE_PWDN", "IO48": "AFE_EN_MCU",
            "IO4": "I2C_SDA", "IO5": "I2C_SCL", "IO6": "IMU_INT1", "IO7": "FG_ALRT", "IO15": "VBUS_DET",
            "IO17": "LED_G", "IO18": "LED_B", "IO19": "USB_D-", "IO20": "USB_D+",
            "IO39": "SD_CLK", "IO38": "SD_CMD", "IO40": "SD_D0", "IO41": "SD_D1", "IO42": "SD_D2", "IO2": "SD_D3",
            "IO1": "SD_DET", "IO0": "BOOT"}
    for pin, net in gpio.items():
        c.connect(net, m[pin])
    c.nc(m, "IO3", "IO8", "IO16", "IO45", "IO46", "IO35", "IO36", "IO37", "TXD0", "RXD0")
    c.newrow()
    c.C("10uF", "+3V3", "GND", "0603"); c.C("100nF", "+3V3", "GND")

    for net, label in (("ESP_EN", "RESET"), ("BOOT", "BOOT/USER")):
        b = c.part("SW", "Switch:SW_Push", label, "Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2",
                   mpn="KMR221GLFS", manufacturer="C&K", desc="Tactile switch")
        c.connect(net, b["1"]); c.connect("GND", b["2"])
    c.R("10k", "+3V3", "BOOT")
    c.newrow()
    c.R("1k", "LED_G", "LEDG_A"); c.LED("green", "LEDG_A", "GND", mpn="150060GS75000", manufacturer="Wurth", desc="Status: recording")
    c.R("1k", "LED_B", "LEDB_A"); c.LED("blue", "LEDB_A", "GND", mpn="150060BS75000", manufacturer="Wurth", desc="Status: link")
    c.R("4.7k", "+3V3", "I2C_SDA"); c.R("4.7k", "+3V3", "I2C_SCL")

    c.newrow()
    imu = c.part("U", "Sensor_Motion:LSM6DSL", "LSM6DSL", "Package_LGA:LGA-14_3x2.5mm_P0.5mm_LayoutBorder3x4y",
                 mpn="LSM6DSLTR", manufacturer="STMicroelectronics", desc="6-axis IMU, I2C 0x6A")
    c.connect("GND", imu["SDO/SA0"], imu["SDX"], imu["SCX"], imu["6"], imu["7"])
    c.connect("+3V3", imu["VDDIO"], imu["VDD"], imu["CS"])
    c.connect("IMU_INT1", imu["INT1"]); c.connect("I2C_SCL", imu["SCL"]); c.connect("I2C_SDA", imu["SDA"])
    c.nc(imu, "INT2", "10", "11")
    c.C("100nF", "+3V3", "GND"); c.C("100nF", "+3V3", "GND")

    c.newrow()
    sd = c.part("J", "Connector:Micro_SD_Card_Det_Hirose_DM3AT", "microSD", "Connector_Card:microSD_HC_Hirose_DM3AT-SF-PEJM5",
                mpn="DM3AT-SF-PEJM5", manufacturer="Hirose", desc="microSD socket with card detect")
    for net, pin in (("SD_D2", "1"), ("SD_D3", "2"), ("SD_CMD", "3"), ("+3V3", "4"), ("SD_CLK", "5"), ("GND", "6"),
                     ("SD_D0", "7"), ("SD_D1", "8"), ("SD_DET", "9"), ("GND", "10"), ("GND", "11")):
        c.connect(net, sd[pin])
    for net in ("SD_CMD", "SD_D0", "SD_D1", "SD_D2", "SD_D3", "SD_DET"):
        c.R("10k", "+3V3", net)
    c.C("10uF", "+3V3", "GND", "0603"); c.C("100nF", "+3V3", "GND")
    c.flag("GND")
    return c


if __name__ == "__main__":
    circ = build()
    errs = circ.erc()
    print(len(circ.parts), "parts,", len(circ.nets), "nets,", len(errs), "ERC errors")
    for e in errs:
        print("  ", e)
