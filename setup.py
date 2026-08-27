from setuptools import find_packages, setup

package_name = 'mvp_mission_bebop'

setup(
    name=package_name,
    version='1.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='P&D',
    maintainer_email='joaomoreirraa@gmail.com',
    description='Missão Autônoma MVP Bebop',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'mission_node = mvp_mission_bebop.flight_controller:main'
        ],
    },
)
