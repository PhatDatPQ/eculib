from setuptools import setup

setup(
    name='eculib',
    version='1.0.37',
    description='A library for K-line',
    url='https://github.com/PhatDatPQ/eculib',
    author='NICE',
    author_email='phatdatpq@gmail.com',
    license='GPL-3',
    packages=['eculib'],
    entry_points={
        'console_scripts': ['eculib=eculib.__main__:Main'],
    },
    install_requires=['pylibftdi','pydispatcher'],
)
