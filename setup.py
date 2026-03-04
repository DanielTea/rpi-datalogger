from setuptools import setup, find_packages

setup(
    name="rpi-datalogger",
    version="0.1.0",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    python_requires=">=3.11",
    install_requires=[
        "python-can>=4.4.0",
        "pyserial>=3.5",
        "supabase>=2.0.0",
        "python-dotenv>=1.0.0",
    ],
)
