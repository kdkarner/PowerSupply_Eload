from PIL import Image, ImageDraw, ImageFont

# Crear lienzo PNG transparente de 256x256
img = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# --- CHESIS PRINCIPAL (Gabinete metálico) ---
draw.rounded_rectangle([12, 16, 244, 240], radius=16, fill='#1E222A', outline='#5C6370', width=4)

# --- PANTALLA DIGITAL (LED Bezel) ---
draw.rounded_rectangle([28, 32, 228, 105], radius=8, fill='#0B0E14', outline='#282C34', width=3)

# Display Voltaje (Verde / Cyan)
draw.text((42, 45), "30.0", fill='#00E676')
draw.text((110, 55), "V", fill='#00E676')

# Display Corriente (Rojo / Ambar)
draw.text((138, 45), "05.0", fill='#FF5252')
draw.text((206, 55), "A", fill='#FF5252')

# Separador central de pantalla
draw.line([128, 35, 128, 102], fill='#1E222A', width=2)

# --- PERILLAS DE AJUSTE (Knobs VOLTAGE / CURRENT) ---
# Perilla Voltaje (Izquierda)
draw.ellipse([46, 120, 98, 172], fill='#3E4451', outline='#ABB2BF', width=3)
draw.line([72, 146, 72, 126], fill='#FFFFFF', width=3) # Indicador

# Perilla Corriente (Derecha)
draw.ellipse([158, 120, 210, 172], fill='#3E4451', outline='#ABB2BF', width=3)
draw.line([184, 146, 200, 130], fill='#FFFFFF', width=3) # Indicador

# --- BORNES DE SALIDA (Banana Jacks) ---
# (+) Rojo
draw.ellipse([42, 192, 68, 218], fill='#FF3B30', outline='#990000', width=2)
draw.ellipse([50, 200, 60, 210], fill='#1E222A')

# (-) Negro
draw.ellipse([115, 192, 141, 218], fill='#2C313A', outline='#5C6370', width=2)
draw.ellipse([123, 200, 133, 210], fill='#1E222A')

# (GND) Verde
draw.ellipse([188, 192, 214, 218], fill='#34C759', outline='#1B5E20', width=2)
draw.ellipse([196, 200, 206, 210], fill='#1E222A')

# --- BOTÓN DE ENCENDIDO (Power Switch) ---
draw.rectangle([20, 140, 28, 160], fill='#FF3B30')

img.save('icon.png')
print("Icono 'icon.png' generado exitosamente.")
