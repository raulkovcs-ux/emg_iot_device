# FAMA-Band top-level tasks
PY ?= python3

.PHONY: test hardware firmware stl coeffs clean

test:
	$(MAKE) -C firmware/components/fama_dsp -f Makefile.host
	cd host && $(PY) -m pytest -q

coeffs:                      ## regenerate firmware filter coefficients from the Python reference
	cd host && $(PY) -m famaband.dsp_ref --emit-header

hardware:
	test -f hardware/tools/freerouting-1.9.0.jar || hardware/tools/get_freerouting.sh
	$(PY) hardware/gen/build_sch.py
	$(PY) hardware/gen/build_pcb.py pod hub
	$(PY) hardware/gen/render.py

firmware:
	cd firmware && idf.py set-target esp32s3 && idf.py build

stl:
	cd mechanical && for p in pod hub_base hub_lid; do openscad -q -D "part=\"$$p\"" -o stl/$$p.stl famaband.scad; done

clean:
	rm -rf firmware/build firmware/build_ble firmware/components/fama_dsp/build hardware/build
