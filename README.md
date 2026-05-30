# UNO R4 + 611-N Load Cell Weight Monitor

This project includes:

- `uno_r4_611n_weight_monitor.ino`: Arduino sketch for UNO R4 with HX711 load cell amplifier.
- `weight_monitor_app.py`: Simple Python desktop app that reads serial weight values from the Arduino and displays them.

## Hardware

- Arduino UNO R4 breakout board
- 611-N load cell
- HX711 amplifier board
- Wires to connect the load cell and HX711 to the R4

## Arduino Connections

- Load cell to HX711 input pins
- HX711 VCC to 5V on UNO R4
- HX711 GND to GND on UNO R4
- HX711 DT to pin 2 on UNO R4
- HX711 SCK to pin 3 on UNO R4

## Usage

1. Upload `uno_r4_611n_weight_monitor.ino` to your UNO R4.
2. Install Python dependencies: `pip install pyserial tkinter` (Tkinter is usually included with Python on macOS).
3. Run the app: `python3 weight_monitor_app.py`.
4. Select the Arduino serial port and press `Connect`.

## Notes

- Calibrate the `scaleFactor` value in the Arduino sketch for your 611-N load cell.
- If the serial port list is empty, verify the board is connected and the correct driver is installed.
