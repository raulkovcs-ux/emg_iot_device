# FAMA-Band top-level tasks
PY ?= python3

.PHONY: test hardware firmware firmware-socket stl coeffs clean

test:
	$(MAKE) -C firmware/components/fama_dsp -f Makefile.host
	cd host && $(PY) -m pytest -q

coeffs:                      ## regenerate firmware filter coefficients from the Python reference
	cd host && $(PY) -m famaband.dsp_ref --emit-header

hardware:
	test -f hardware/tools/freerouting-1.9.0.jar || hardware/tools/get_freerouting.sh
	$(PY) hardware/gen/build_sch.py
	$(PY) hardware/gen/build_pcb.py pod hub hub_flex
	$(PY) hardware/gen/render.py

firmware:
	cd firmware && idf.py set-target esp32s3 && idf.py build

firmware-socket:
	cd firmware && idf.py -B build_socket -D SDKCONFIG=build_socket/sdkconfig -D SDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.socket" build

stl:
	cd mechanical && for p in pod hub_base hub_lid; do openscad -q -D "part=\"$$p\"" -o stl/$$p.stl famaband.scad; done
	cd mechanical && for p in inner_socket cartridge hub_carrier frame_anterior frame_posterior service_door; do openscad -q -D "part=\"$$p\"" -o stl/socket_$$p.stl socket.scad; done

clean:
	rm -rf firmware/build firmware/build_ble firmware/build_socket firmware/components/fama_dsp/build hardware/build
