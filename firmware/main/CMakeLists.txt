idf_component_register(
    SRCS app_main.c ads1298.c power.c imu.c storage.c link.c td.c
    INCLUDE_DIRS .
    REQUIRES fama_dsp driver esp_driver_sdmmc fatfs nvs_flash esp_wifi esp_netif esp_event esp_timer bt lwip)
