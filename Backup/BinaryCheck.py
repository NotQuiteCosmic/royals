input = input()
binary = bin(int(input))
print("000"[0:3-(len(binary) - 2)] + binary[2:len(binary)])