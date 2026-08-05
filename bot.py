def calcular_precio_total(cantidad):
    if cantidad <= 0:
        return 0
    
    total = 0
    restantes = cantidad

    # Primero sacamos todos los bloques posibles de 5 (80 reales)
    while restantes >= 5:
        total += 80
        restantes -= 5

    # Si quedan 4
    if restantes == 4:
        total += 70
        restantes = 0
    # Si quedan 3
    elif restantes == 3:
        total += 50
        restantes = 0
    # Si quedan 2
    elif restantes == 2:
        total += 30
        restantes = 0

    # Si queda 1 suelto
    if restantes == 1:
        total += 20
        restantes = 0

    return total
